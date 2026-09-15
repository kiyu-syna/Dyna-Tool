from profile_automation.watchers.douyin_video import DouyinVideo


class UploadSkipped(RuntimeError):
    """The platform intentionally skipped this video and must not retry it."""


class UploadUnconfirmed(RuntimeError):
    """The publish action was sent, but the platform never confirmed completion."""


class BaseUploader:
    def upload(self, video_path: str, video: DouyinVideo, profile: dict) -> bool:
        """
        Upload a video to the destination platform.
        
        Args:
            video_path: Local path to the downloaded MP4 file.
            video: DouyinVideo metadata object.
            profile: Profile configuration dict.
            
        Returns:
            bool: True if upload succeeds, False otherwise.
        """
        raise NotImplementedError
