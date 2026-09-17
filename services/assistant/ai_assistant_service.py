"""Desktop bridge to the server-hosted Dyna AI gateway."""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

SUPPORTED_ACTIONS = {
    "start_profile",
    "stop_profile",
    "refresh_readiness",
}
SUPPORTED_PLATFORMS = {"youtube", "tiktok", "facebook"}
PROPOSAL_TTL_SECONDS = 10 * 60


def _unwrap_caption_text(value: Any) -> str:
    caption = str(value or "").strip()
    decoder = json.JSONDecoder()
    for _ in range(4):
        if caption.startswith("```") and caption.endswith("```"):
            caption = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", caption)
            caption = re.sub(r"\s*```$", "", caption).strip()

        parsed: Any = None
        try:
            parsed = json.loads(caption)
        except (json.JSONDecodeError, TypeError):
            if caption.startswith(("{", "[", '"')):
                try:
                    parsed, _end = decoder.raw_decode(caption)
                except json.JSONDecodeError:
                    parsed = None

        if isinstance(parsed, dict):
            nested = next(
                (
                    parsed.get(key)
                    for key in ("caption", "reply", "content", "output")
                    if isinstance(parsed.get(key), str) and parsed.get(key).strip()
                ),
                None,
            )
            if nested is None:
                break
            caption = nested.strip()
            continue
        if isinstance(parsed, str):
            caption = parsed.strip()
            continue
        break
    return caption


def _unwrap_json_text(value: Any) -> Any:
    """Extract a JSON object/array from a model response without accepting prose."""
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        decoder = json.JSONDecoder()
        for marker in ("[", "{"):
            start = text.find(marker)
            if start < 0:
                continue
            try:
                payload, _end = decoder.raw_decode(text[start:])
                return payload
            except json.JSONDecodeError:
                continue
    return None


def _translation_map_from_reply(
    value: Any,
    source_items: list[dict[str, str]],
    *,
    allow_partial: bool = False,
) -> dict[str, str] | None:
    expected_ids = [item["id"] for item in source_items]
    expected = set(expected_ids)
    current: Any = value
    fallback_text = str(value or "").strip()

    for _ in range(6):
        if isinstance(current, str):
            fallback_text = current.strip()
            parsed = _unwrap_json_text(current)
            if parsed is None or parsed == current:
                break
            current = parsed
            continue
        if isinstance(current, dict):
            direct = {
                str(key): str(text).strip()
                for key, text in current.items()
                if str(key) in expected and isinstance(text, str) and text.strip()
            }
            if expected.issubset(direct):
                return direct
            nested = next(
                (
                    current.get(key)
                    for key in ("translations", "items", "data", "reply", "content", "output", "result")
                    if current.get(key) not in (None, "")
                ),
                None,
            )
            if nested is None:
                return direct if allow_partial and direct else None
            current = nested
            continue
        if isinstance(current, list):
            translated: dict[str, str] = {}
            if all(isinstance(item, str) for item in current) and len(current) == len(expected_ids):
                translations = {
                    item_id: str(text).strip()
                    for item_id, text in zip(expected_ids, current)
                    if str(text).strip()
                }
                if len(translations) == len(expected_ids):
                    return translations
            for index, item in enumerate(current):
                if not isinstance(item, dict):
                    continue
                item_id = str(
                    item.get("id")
                    or item.get("key")
                    or item.get("index")
                    or (expected_ids[index] if index < len(expected_ids) else "")
                )
                text = next(
                    (
                        item.get(key)
                        for key in (
                            "text",
                            "translation",
                            "translated_text",
                            "target",
                            "content",
                            "output",
                        )
                        if isinstance(item.get(key), str) and item.get(key).strip()
                    ),
                    "",
                )
                if item_id in expected and text:
                    translated[item_id] = str(text).strip()
            if expected.issubset(translated):
                return translated
            return translated if allow_partial and translated else None
        break

    text = fallback_text
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    delimited: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([^|\t]{1,64}?)(?:\s*\|\|\|\s*|\t+)(.+)$", line)
        if match and match.group(1).strip() in expected:
            delimited[match.group(1).strip()] = match.group(2).strip()
    if expected.issubset(delimited):
        return delimited
    if allow_partial and delimited:
        return delimited
    if len(lines) == len(expected_ids):
        cleaned_lines = [
            re.sub(r"^\s*(?:\d+|[-*])[\s.):\-]+", "", line).strip()
            for line in lines
        ]
        if all(cleaned_lines):
            return dict(zip(expected_ids, cleaned_lines))
    if len(expected_ids) == 1 and text and not text.startswith(("{", "[")):
        single_text = re.sub(
            r"^(?:bản dịch|translation|translated text)\s*:\s*",
            "",
            text.strip(),
            flags=re.IGNORECASE,
        ).strip().strip('"')
        acknowledgement = re.fullmatch(
            r"(?:đã\s+)?dịch(?:\s+xong)?[.!]?|translation\s+(?:is\s+)?done[.!]?|done[.!]?",
            single_text,
            flags=re.IGNORECASE,
        )
        if single_text and acknowledgement is None:
            return {expected_ids[0]: single_text}
    return None


class AiAssistantError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503, retry_after: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass
class _Proposal:
    actions: list[dict[str, Any]]
    expires_at: float


def _safe_detail(response: requests.Response, fallback: str) -> str:
    try:
        detail = response.json().get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    except (ValueError, AttributeError, TypeError):
        pass
    return fallback


def _profile_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"\d+", normalized):
        raise ValueError("Profile ID không hợp lệ")
    return normalized


def _normalize_action(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    action_type = str(action.get("type") or "").strip()
    args = action.get("args")
    if action_type not in SUPPORTED_ACTIONS or not isinstance(args, dict):
        return None
    try:
        if action_type in {"start_profile", "stop_profile"}:
            clean_args = {"profile_id": _profile_id(args.get("profile_id"))}
        else:
            raw_targets = args.get("targets")
            if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 50:
                return None
            targets: list[dict[str, Any]] = []
            seen: set[str] = set()
            for target in raw_targets:
                if not isinstance(target, dict):
                    return None
                profile_id = _profile_id(target.get("profile_id"))
                platforms = list(
                    dict.fromkeys(
                        str(platform or "").strip().lower()
                        for platform in (target.get("platforms") or [])
                    )
                )
                if profile_id in seen or not platforms or any(
                    platform not in SUPPORTED_PLATFORMS for platform in platforms
                ):
                    return None
                seen.add(profile_id)
                targets.append({"profile_id": profile_id, "platforms": platforms})
            clean_args = {"targets": targets}
    except ValueError:
        return None
    return {"type": action_type, "args": clean_args}


class AiAssistantService:
    def __init__(self) -> None:
        self._proposals: dict[str, _Proposal] = {}
        self._lock = threading.RLock()

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> dict[str, Any]:
        raise AiAssistantError(
            "Trợ lý AI máy chủ đã được tắt cùng hệ thống tài khoản/thương mại.",
            status_code=410,
        )

    def generate_caption(
        self,
        *,
        original_description: str,
        instruction: str,
        video_label: str = "",
    ) -> dict[str, Any]:
        user_instruction = str(instruction or "").strip()
        source = str(original_description or "").strip()
        label = str(video_label or "").strip()
        prompt = (
            "Bạn đang viết mô tả để đăng một video lên mạng xã hội.\n"
            "Hãy tạo đúng MỘT mô tả hoàn chỉnh, tự nhiên và phù hợp riêng cho video này.\n"
            "Không giải thích, không dùng dấu ngoặc kép bao quanh, không thêm tiêu đề như "
            "'Mô tả:' và chỉ trả về nội dung mô tả cuối cùng.\n"
            f"Tên/ID video: {label or 'Không có'}\n"
            f"Yêu cầu của người dùng: {user_instruction or 'Viết lại hấp dẫn, ngắn gọn và giữ các hashtag quan trọng.'}\n"
            f"Mô tả gốc của video:\n{source or '(Không có mô tả gốc; hãy dựa vào tên video và yêu cầu người dùng.)'}"
        )
        result = self.chat(
            [{"role": "user", "content": prompt[:12000]}],
            {
                "mode": "social_caption_generation",
                "video_label": label[:512],
            },
        )
        caption = _unwrap_caption_text(result.get("reply"))
        caption = re.sub(
            r"^(?:mô tả|caption|description)\s*:\s*",
            "",
            caption,
            flags=re.IGNORECASE,
        ).strip()
        if len(caption) >= 2 and caption[0] == caption[-1] and caption[0] in {'"', "'"}:
            caption = caption[1:-1].strip()
        if not caption:
            raise AiAssistantError("DynaAI chưa tạo được mô tả cho video này")
        return {
            "caption": caption[:10000],
            "provider": str(result.get("provider") or ""),
            "model": str(result.get("model") or ""),
        }

    def translate_subtitles(
        self,
        subtitles: list[dict[str, str]],
        *,
        source_language: str,
        target_language: str,
    ) -> list[dict[str, str]]:
        clean_items = [
            {
                "id": str(item.get("id") or "")[:64],
                "text": str(item.get("text") or "").strip()[:4000],
            }
            for item in subtitles[:40]
            if str(item.get("id") or "").strip() and str(item.get("text") or "").strip()
        ]
        if not clean_items:
            return []
        language_context = (
            f"Ngôn ngữ nguồn: {str(source_language or 'auto')[:32]}\n"
            f"Ngôn ngữ đích: {str(target_language or '')[:32]}\n"
        )
        prompt = (
            "Bạn là bộ máy dịch phụ đề video.\n"
            f"{language_context}"
            "Giữ nguyên ý nghĩa, giọng điệu, tên riêng và số liệu. Viết tự nhiên, ngắn gọn "
            "để đọc kịp trên video. Không gộp, tách hoặc đổi ID của các dòng.\n"
            "Chỉ trả về đúng một dòng cho mỗi ID theo định dạng ID|||BẢN DỊCH. "
            "Không dùng JSON, markdown, đánh số hoặc giải thích.\n"
            + "\n".join(f"{item['id']}|||{item['text']}" for item in clean_items)
        )
        result = self.chat(
            [{"role": "user", "content": prompt[:12000]}],
            {
                "mode": "subtitle_translation",
                "source_language": str(source_language or "auto")[:32],
                "target_language": str(target_language or "")[:32],
                "line_count": len(clean_items),
            },
        )
        translated = _translation_map_from_reply(
            result.get("reply"),
            clean_items,
            allow_partial=True,
        ) or {}
        missing = [item for item in clean_items if item["id"] not in translated]
        if missing:
            retry_prompt = (
                "Phản hồi trước bị thiếu dòng. Hãy dịch CHỈ các dòng dưới đây.\n"
                f"{language_context}"
                "Mỗi ID phải xuất hiện đúng một lần theo định dạng ID|||BẢN DỊCH. "
                "Không trả lời 'dịch xong', không dùng JSON, markdown hoặc giải thích.\n"
                + "\n".join(f"{item['id']}|||{item['text']}" for item in missing)
            )
            retry = self.chat(
                [{"role": "user", "content": retry_prompt[:12000]}],
                {
                    "mode": "subtitle_translation_repair",
                    "source_language": str(source_language or "auto")[:32],
                    "target_language": str(target_language or "")[:32],
                    "line_count": len(missing),
                },
            )
            repaired = _translation_map_from_reply(
                retry.get("reply"),
                missing,
                allow_partial=True,
            ) or {}
            translated.update(repaired)

        missing = [item for item in clean_items if item["id"] not in translated]
        if missing and len(clean_items) > 1:
            midpoint = max(1, len(missing) // 2)
            for smaller_batch in (missing[:midpoint], missing[midpoint:]):
                if not smaller_batch:
                    continue
                for item in self.translate_subtitles(
                    smaller_batch,
                    source_language=source_language,
                    target_language=target_language,
                ):
                    translated[item["id"]] = item["text"]

        missing = [item["id"] for item in clean_items if item["id"] not in translated]
        if missing:
            raise AiAssistantError(f"DynaAI chưa dịch được dòng phụ đề {missing[0]}.")
        return [{"id": item["id"], "text": translated[item["id"]]} for item in clean_items]

    def _store_proposal(self, actions: list[dict[str, Any]]) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            self._proposals[token] = _Proposal(
                actions=actions,
                expires_at=now + PROPOSAL_TTL_SECONDS,
            )
        return token

    def consume_proposal(self, token: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            proposal = self._proposals.pop(str(token or "").strip(), None)
        if proposal is None:
            raise AiAssistantError(
                "Đề xuất đã hết hạn hoặc đã được sử dụng. Hãy hỏi Trợ lý AI lại.",
                status_code=409,
            )
        return proposal.actions

    def _remove_expired(self, now: float) -> None:
        expired = [token for token, item in self._proposals.items() if item.expires_at <= now]
        for token in expired:
            self._proposals.pop(token, None)
