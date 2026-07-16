from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from core.utils import logger
from services.workload_coordinator import workload_slot


VALID_PRESETS = (
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)
DEFAULT_PRESET = "slow"
DEFAULT_CRF = 16


class YouTubeShortsConversionError(RuntimeError):
    pass


def _resolve_ffmpeg() -> str:
    for candidate in (shutil.which("ffmpeg"), "C:/ffmpeg/bin/ffmpeg.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise YouTubeShortsConversionError(
        "FFmpeg was not found in PATH or at C:/ffmpeg/bin/ffmpeg.exe."
    )


def _validated_settings(preset: str, crf: int) -> tuple[str, int]:
    preset = str(preset or DEFAULT_PRESET).strip().lower()
    if preset not in VALID_PRESETS:
        raise YouTubeShortsConversionError(f"Invalid FFmpeg preset: {preset}")
    try:
        crf = int(crf)
    except (TypeError, ValueError) as exc:
        raise YouTubeShortsConversionError(f"Invalid CRF value: {crf}") from exc
    if not 0 <= crf <= 51:
        raise YouTubeShortsConversionError("CRF must be between 0 and 51.")
    return preset, crf


def make_output_path(input_path: str | Path) -> Path:
    source = Path(input_path).resolve()
    candidate = source.with_name(f"{source.stem}_youtube_shorts.mp4")
    index = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}_youtube_shorts_{index}.mp4")
        index += 1
    return candidate


def convert_to_youtube_shorts(
    input_path: str | Path,
    *,
    preset: str = DEFAULT_PRESET,
    crf: int = DEFAULT_CRF,
    output_path: str | Path | None = None,
    profile_id: str = "",
    video_id: str = "",
    priority: int = 100,
    cancel_event=None,
) -> Path:
    """Convert a video to 1080x1920 with a blurred 9:16 background."""
    source = Path(input_path).resolve()
    if not source.is_file():
        raise YouTubeShortsConversionError(f"Source video was not found: {source}")

    preset, crf = _validated_settings(preset, crf)
    target = Path(output_path).resolve() if output_path else make_output_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    # This is the same blur-background graph used by the standalone converter.
    filter_graph = (
        "[0:v]split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "boxblur=luma_radius=32:luma_power=2:chroma_radius=32:chroma_power=2[bg];"
        "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
    )
    command = [
        _resolve_ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(target),
    ]

    logger.info(
        "[YouTube Shorts] Starting 9:16 conversion: %s -> %s (preset=%s, crf=%s)",
        source,
        target,
        preset,
        crf,
    )
    try:
        with workload_slot(
            "ffmpeg",
            profile_id=profile_id,
            video_id=video_id or source.stem,
            platform="youtube",
            priority=priority,
            cancel_event=cancel_event,
        ):
            result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise YouTubeShortsConversionError(f"Could not run FFmpeg: {exc}") from exc

    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        detail = (result.stderr or "FFmpeg did not create an output file.").strip()
        raise YouTubeShortsConversionError(f"Video conversion failed: {detail}")

    logger.info("[YouTube Shorts] 9:16 conversion completed: %s", target)
    return target
