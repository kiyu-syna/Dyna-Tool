from profile_automation.uploaders.tiktok_uploader import TikTokUploader
from profile_automation.uploaders.facebook_uploader import FacebookUploader
from profile_automation.uploaders.youtube_uploader import YouTubeUploader
from profile_automation.uploaders.base_uploader import UploadSkipped
from profile_automation.watchers.douyin_profile_monitor import DouyinVideo
from core.utils import logger
from services.workload_coordinator import WorkloadCancelled, workload_slot


def enabled_platform_names(profile: dict) -> list[str]:
    enabled = []
    if profile.get("tiktok", {}).get("enabled", True):
        enabled.append("tiktok")
    if profile.get("facebook", {}).get("enabled", False):
        enabled.append("facebook")
    if profile.get("youtube", {}).get("enabled", False):
        enabled.append("youtube")
    return enabled


def all_enabled_uploads_succeeded(results: dict, profile: dict) -> bool:
    enabled = enabled_platform_names(profile)
    return bool(enabled) and all(
        results.get(platform, False) is True or results.get(platform) == "skipped"
        for platform in enabled
    )


def primary_upload_succeeded(results: dict, profile: dict) -> bool:
    """Use TikTok as primary, but support profiles where TikTok is disabled."""
    if profile.get("tiktok", {}).get("enabled", True):
        return bool(results.get("tiktok", False))

    return any(
        bool(platform_cfg.get("enabled", False)) and bool(results.get(platform_name, False))
        for platform_name, platform_cfg in (
            ("facebook", profile.get("facebook", {})),
            ("youtube", profile.get("youtube", {})),
        )
    )

class UploadPipeline:
    def __init__(self):
        self.tiktok_uploader = TikTokUploader()
        self.facebook_uploader = FacebookUploader()
        self.youtube_uploader = YouTubeUploader()

    def run(
        self,
        video_path: str,
        video: DouyinVideo,
        profile: dict,
        successful_platforms=None,
        on_platform_status=None,
        priority: int = 100,
        cancel_event=None,
    ) -> dict:
        results = {}
        successful_platforms = set(successful_platforms or ())
        profile["_workload_priority"] = int(priority)
        profile["_runtime_cancel_event"] = cancel_event
        uploaders = {
            "tiktok": self.tiktok_uploader,
            "facebook": self.facebook_uploader,
            "youtube": self.youtube_uploader,
        }

        for platform in enabled_platform_names(profile):
            if platform in successful_platforms:
                results[platform] = True
                logger.info(
                    "[Upload Pipeline] Bỏ qua %s cho video %s vì đã đăng thành công trước đó.",
                    platform,
                    video.aweme_id,
                )
                continue

            try:
                with workload_slot(
                    "upload",
                    profile_id=str(profile.get("id") or ""),
                    video_id=str(video.aweme_id),
                    platform=platform,
                    priority=priority,
                    cancel_event=cancel_event,
                ):
                    if on_platform_status:
                        on_platform_status(platform, "uploading", "")
                    succeeded = bool(uploaders[platform].upload(video_path, video, profile))
                results[platform] = succeeded
                if on_platform_status:
                    on_platform_status(
                        platform,
                        "success" if succeeded else "failed",
                        "" if succeeded else "Uploader trả về thất bại.",
                    )
            except UploadSkipped as exc:
                reason = str(exc)
                results[platform] = "skipped"
                logger.warning(
                    "[Upload Pipeline] Bỏ qua %s cho video %s: %s",
                    platform,
                    video.aweme_id,
                    reason,
                )
                if on_platform_status:
                    on_platform_status(platform, "skipped", reason)
            except WorkloadCancelled:
                if on_platform_status:
                    on_platform_status(platform, "pending", "")
                raise
            except Exception as e:
                logger.error(f"[Upload Pipeline] {platform} upload error: {e}")
                results[platform] = False
                if on_platform_status:
                    on_platform_status(platform, "failed", str(e))

        return results
