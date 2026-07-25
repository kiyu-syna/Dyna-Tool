"""Desktop worker for actions explicitly approved through the shared Telegram bot."""

from __future__ import annotations

import logging
import threading
from typing import Any

from services.integrations import telegram_service
from services.publishing.manual_publish_service import ManualPublishTarget, ManualPublishService
from services.runtime.tracking_runtime_service import TrackingRuntimeService
from profile_automation.pipeline.video_job_store import VideoJobStore

logger = logging.getLogger(__name__)
POLL_SECONDS = 3


class TelegramRemoteActionWorker:
    def __init__(self, runtime: TrackingRuntimeService, manual_publish: ManualPublishService, jobs: VideoJobStore | None = None) -> None:
        self.runtime = runtime
        self.manual_publish = manual_publish
        self.jobs = jobs or VideoJobStore()
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, daemon=True, name="dyna-telegram-actions").start()

    def _run(self) -> None:
        while True:
            try:
                request = telegram_service.claim_remote_action()
                if request:
                    self._process(request)
            except Exception:
                logger.exception("Telegram remote-action worker failed")
            threading.Event().wait(POLL_SECONDS)

    def _process(self, request: dict[str, Any]) -> None:
        request_id = str(request.get("request_id") or "")
        actions = request.get("actions")
        if not request_id or not isinstance(actions, list) or not actions:
            return
        results: list[dict[str, Any]] = []
        for action in actions[:8]:
            action_type = str((action or {}).get("type") or "")
            args = (action or {}).get("args") or {}
            try:
                if action_type == "start_profile":
                    self.runtime.start_profile(str(args["profile_id"]))
                elif action_type == "stop_profile":
                    self.runtime.stop_profile(str(args["profile_id"]))
                elif action_type == "refresh_readiness":
                    self.manual_publish.refresh_readiness(
                        tuple(
                            ManualPublishTarget(
                                profile_id=str(target["profile_id"]),
                                platforms=tuple(target["platforms"]),
                            )
                            for target in args["targets"]
                        ),
                        force=True,
                    )
                elif action_type == "cancel_video":
                    job = self.jobs.cancel_job(
                        str(args["profile_id"]), str(args["video_id"]),
                        reason="Người dùng huỷ video từ Telegram.",
                    )
                    if job is None:
                        raise ValueError("Không tìm thấy video hoặc video chưa thể huỷ.")
                else:
                    raise ValueError("Thao tác Telegram không được hỗ trợ")
                results.append({"type": action_type, "ok": True, "error": ""})
            except Exception as exc:
                logger.warning("Telegram action failed type=%s: %s", action_type, exc)
                results.append({"type": action_type or "unknown", "ok": False, "error": str(exc)[:800]})
        telegram_service.complete_remote_action(request_id, results)
