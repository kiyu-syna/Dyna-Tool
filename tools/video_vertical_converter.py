from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


LOGGER = logging.getLogger(__name__)


class VideoConversionError(Exception):
    """Base exception for video conversion failures."""


class FFmpegNotFoundError(VideoConversionError):
    """Raised when ffmpeg or ffprobe cannot be found."""


class VideoProbeError(VideoConversionError):
    """Raised when the input video cannot be probed."""


@dataclass(frozen=True, slots=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float | None
    duration: float | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None


@dataclass(frozen=True, slots=True)
class BatchConversionResult:
    input_path: Path
    output_path: Path
    success: bool
    error: str | None = None


class VideoVerticalConverter:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        preset: str = "medium",
        crf: int = 20,
        threads: int | None = None,
        blur_strength: int = 20,
        audio_bitrate: str = "192k",
        timeout: int = 3600,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.preset = preset
        self.crf = crf
        self.threads = threads
        self.blur_strength = blur_strength
        self.audio_bitrate = audio_bitrate
        self.timeout = timeout
        self._video_encoder_cache: str | None = None

    def validate_ffmpeg(self) -> None:
        ffmpeg_bin = shutil.which(self.ffmpeg_path) if Path(self.ffmpeg_path).name == self.ffmpeg_path else self.ffmpeg_path
        ffprobe_bin = shutil.which(self.ffprobe_path) if Path(self.ffprobe_path).name == self.ffprobe_path else self.ffprobe_path

        if not ffmpeg_bin:
            raise FFmpegNotFoundError("Khong tim thay ffmpeg trong PATH hoac duong dan da cung cap.")
        if not ffprobe_bin:
            raise FFmpegNotFoundError("Khong tim thay ffprobe trong PATH hoac duong dan da cung cap.")

        LOGGER.debug("Validated ffmpeg=%s ffprobe=%s", ffmpeg_bin, ffprobe_bin)

    def _resolve_h264_encoder(self) -> str:
        if self._video_encoder_cache:
            return self._video_encoder_cache

        command = [self.ffmpeg_path, "-hide_banner", "-encoders"]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=30,
            )
        except Exception as exc:
            raise FFmpegNotFoundError(f"Khong the doc danh sach encoder tu ffmpeg: {exc}") from exc

        encoder_output = result.stdout
        candidates = [
            "libx264",
            "h264_mf",
            "h264_nvenc",
            "h264_amf",
            "h264_qsv",
        ]
        for encoder in candidates:
            if encoder in encoder_output:
                self._video_encoder_cache = encoder
                LOGGER.info("Selected H.264 encoder: %s", encoder)
                return encoder

        raise VideoConversionError("Khong tim thay encoder H.264 kha dung trong ffmpeg.")

    def get_video_info(self, input_path: str | Path) -> VideoInfo:
        self.validate_ffmpeg()

        source = Path(input_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Khong tim thay file video: {source}")
        if not source.is_file():
            raise FileNotFoundError(f"Duong dan khong phai file: {source}")

        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(source),
        ]
        LOGGER.debug("Running ffprobe: %s", command)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise VideoProbeError(f"ffprobe bi timeout khi doc file: {source}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "Khong ro loi"
            raise VideoProbeError(f"Khong the doc thong tin video {source}: {stderr}") from exc

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise VideoProbeError(f"ffprobe tra ve JSON khong hop le cho file: {source}") from exc

        streams = payload.get("streams", [])
        format_info = payload.get("format", {})
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

        if not video_stream:
            raise VideoProbeError(f"File khong chua video stream hop le: {source}")

        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        if width <= 0 or height <= 0:
            raise VideoProbeError(f"Khong the xac dinh kich thuoc video: {source}")

        fps = self._parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        duration_raw = format_info.get("duration") or video_stream.get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "N/A", "") else None

        info = VideoInfo(
            path=source,
            width=width,
            height=height,
            fps=fps,
            duration=duration,
            has_audio=audio_stream is not None,
            video_codec=video_stream.get("codec_name"),
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        )
        LOGGER.info(
            "Loaded video info path=%s size=%sx%s fps=%s has_audio=%s",
            info.path,
            info.width,
            info.height,
            info.fps,
            info.has_audio,
        )
        return info

    def build_ffmpeg_command(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        overwrite: bool = True,
        preset: str | None = None,
        crf: int | None = None,
        threads: int | None = None,
    ) -> list[str]:
        info = self.get_video_info(input_path)
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        selected_preset = preset or self.preset
        selected_crf = self.crf if crf is None else crf
        selected_threads = self.threads if threads is None else threads
        video_encoder = self._resolve_h264_encoder()

        blur_radius = max(1, self.blur_strength)
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            f"gblur=sigma={blur_radius}[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,"
            "format=yuv420p[vout]"
        )

        command: list[str] = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if overwrite else "-n",
            "-i",
            str(info.path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "0:a?" if info.has_audio else "0:a?",
            "-c:v",
            video_encoder,
            "-c:a",
            "aac",
            "-b:a",
            self.audio_bitrate,
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
        ]

        if video_encoder == "libx264":
            command.extend(
                [
                    "-preset",
                    selected_preset,
                    "-crf",
                    str(selected_crf),
                ]
            )
        elif video_encoder in {"h264_nvenc", "h264_amf", "h264_qsv"}:
            command.extend(
                [
                    "-b:v",
                    "0",
                    "-cq",
                    str(selected_crf),
                ]
            )
        elif video_encoder == "h264_mf":
            command.extend(["-q:v", str(selected_crf)])

        if video_encoder == "h264_nvenc":
            command.extend(["-preset", "p5"])
        elif video_encoder == "h264_amf":
            command.extend(["-quality", "quality"])

        if selected_threads is not None and selected_threads > 0:
            command.extend(["-threads", str(selected_threads)])

        command.append(str(destination))

        LOGGER.debug("Built ffmpeg command: %s", command)
        return command

    def convert(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        overwrite: bool = True,
        timeout: int | None = None,
        preset: str | None = None,
        crf: int | None = None,
        threads: int | None = None,
    ) -> Path:
        command = self.build_ffmpeg_command(
            input_path,
            output_path,
            overwrite=overwrite,
            preset=preset,
            crf=crf,
            threads=threads,
        )
        effective_timeout = timeout or self.timeout
        destination = Path(output_path).expanduser().resolve()

        LOGGER.info("Starting conversion input=%s output=%s", input_path, destination)
        try:
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise VideoConversionError(
                f"FFmpeg bi timeout sau {effective_timeout} giay khi convert {input_path}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "Khong ro loi encode"
            raise VideoConversionError(f"FFmpeg encode that bai: {stderr}") from exc

        if not destination.exists() or destination.stat().st_size == 0:
            raise VideoConversionError(f"FFmpeg da chay xong nhung file dau ra khong hop le: {destination}")

        LOGGER.info("Conversion completed output=%s", destination)
        return destination

    def batch_convert(
        self,
        input_files: Sequence[str | Path] | Iterable[str | Path],
        output_dir: str | Path,
        *,
        suffix: str = "_vertical",
        overwrite: bool = True,
        timeout: int | None = None,
        preset: str | None = None,
        crf: int | None = None,
        threads: int | None = None,
    ) -> list[BatchConversionResult]:
        destination_dir = Path(output_dir).expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)

        results: list[BatchConversionResult] = []
        for item in input_files:
            source = Path(item).expanduser().resolve()
            output_path = destination_dir / f"{source.stem}{suffix}.mp4"
            try:
                self.convert(
                    source,
                    output_path,
                    overwrite=overwrite,
                    timeout=timeout,
                    preset=preset,
                    crf=crf,
                    threads=threads,
                )
                results.append(
                    BatchConversionResult(
                        input_path=source,
                        output_path=output_path,
                        success=True,
                    )
                )
            except Exception as exc:
                LOGGER.exception("Batch conversion failed input=%s", source)
                results.append(
                    BatchConversionResult(
                        input_path=source,
                        output_path=output_path,
                        success=False,
                        error=str(exc),
                    )
                )
        return results

    @staticmethod
    def _parse_fps(frame_rate: str | None) -> float | None:
        if not frame_rate or frame_rate in {"0/0", "N/A"}:
            return None
        if "/" in frame_rate:
            numerator_raw, denominator_raw = frame_rate.split("/", 1)
            numerator = float(numerator_raw)
            denominator = float(denominator_raw)
            if denominator == 0:
                return None
            return numerator / denominator
        return float(frame_rate)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert any video to vertical 1080x1920 with blurred background.")
    parser.add_argument("input", nargs="+", help="One or more input video files.")
    parser.add_argument("-o", "--output", required=True, help="Output file path for single input, or output directory for batch.")
    parser.add_argument("--preset", default="medium", help="FFmpeg x264 preset. Example: veryfast, medium, slow.")
    parser.add_argument("--crf", type=int, default=20, help="CRF value for H264 quality.")
    parser.add_argument("--threads", type=int, default=None, help="Number of ffmpeg threads.")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout in seconds per conversion.")
    parser.add_argument("--blur-strength", type=int, default=20, help="Blur radius for the background layer.")
    parser.add_argument("--ffmpeg-path", default="ffmpeg", help="Path to ffmpeg executable.")
    parser.add_argument("--ffprobe-path", default="ffprobe", help="Path to ffprobe executable.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level.")
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    parser = _build_cli_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level)

    converter = VideoVerticalConverter(
        ffmpeg_path=args.ffmpeg_path,
        ffprobe_path=args.ffprobe_path,
        preset=args.preset,
        crf=args.crf,
        threads=args.threads,
        blur_strength=args.blur_strength,
        timeout=args.timeout,
    )

    try:
        if len(args.input) == 1:
            output_path = converter.convert(args.input[0], args.output)
            LOGGER.info("Done: %s", output_path)
        else:
            results = converter.batch_convert(args.input, args.output)
            failures = [item for item in results if not item.success]
            for item in results:
                if item.success:
                    LOGGER.info("OK: %s -> %s", item.input_path, item.output_path)
                else:
                    LOGGER.error("FAIL: %s -> %s | %s", item.input_path, item.output_path, item.error)
            if failures:
                return 1
        return 0
    except Exception as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
