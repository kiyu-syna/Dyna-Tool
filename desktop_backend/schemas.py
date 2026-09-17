from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProfilePayload(BaseModel):
    profile: dict[str, Any]


class CreateProfilePayload(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""


class SourceTestPayload(BaseModel):
    source: dict[str, Any]


class SeenStatePayload(BaseModel):
    data: dict[str, Any]


class DiagnosticPayload(BaseModel):
    profile_id: str | None = None


class BrowserRuntimeInstallPayload(BaseModel):
    source_executable: str = Field(min_length=1)
    runtime_id: str = ""


class LocalProfileSetupPayload(BaseModel):
    executable_path: str = ""


class LocalProfileCheckPayload(BaseModel):
    repair_stale_locks: bool = True


class TestUploadPayload(BaseModel):
    confirmed: bool = False


class SettingsPayload(BaseModel):
    settings: dict[str, Any]


class JobActionPayload(BaseModel):
    profile_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)


class ExtensionJobPayload(BaseModel):
    profile_id: str = Field(min_length=1, pattern=r"^\d+$")
    video_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    file_path: str = Field(min_length=1, max_length=4096)
    source_url: str = Field(default="", max_length=4096)
    description: str = Field(default="", max_length=10000)
    create_time: int = 0
    duration_ms: int = 0
    like_count: int = 0
    play_count: int = 0
    author_uid: str = Field(default="", max_length=256)
    author_nickname: str = Field(default="", max_length=512)
    download_url: str = Field(default="", max_length=8192)
    download_id: int = 0


class ManualPublishTargetPayload(BaseModel):
    profile_id: str = Field(min_length=1, pattern=r"^\d+$")
    platforms: list[str] = Field(min_length=1, max_length=3)


class ManualPublishItemPayload(BaseModel):
    file_path: str = Field(min_length=1, max_length=4096)
    caption: str = Field(min_length=1, max_length=10000)
    scheduled_at: str = Field(default="", max_length=64)


class ManualPublishPayload(BaseModel):
    file_paths: list[str] = Field(default_factory=list, max_length=50)
    caption: str = Field(default="", max_length=10000)
    batch_name: str = Field(default="", max_length=160)
    items: list[ManualPublishItemPayload] = Field(default_factory=list, max_length=50)
    targets: list[ManualPublishTargetPayload] = Field(min_length=1, max_length=50)


class DouyinSelectionCreatePayload(BaseModel):
    source_url: str = Field(min_length=1, max_length=4096)


class DouyinSelectionVideoPayload(BaseModel):
    video_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    source_url: str = Field(default="", max_length=4096)
    description: str = Field(default="", max_length=10000)
    author_uid: str = Field(default="", max_length=256)
    author_nickname: str = Field(default="", max_length=512)
    create_time: int = 0
    duration_ms: int = 0
    like_count: int = 0
    play_count: int = 0
    thumbnail_url: str = Field(default="", max_length=8192)
    download_url: str = Field(default="", max_length=8192)
    download_urls: list[str] = Field(default_factory=list, max_length=12)
    referer: str = Field(default="", max_length=4096)
    user_agent: str = Field(default="", max_length=1024)
    selected_order: int = Field(default=0, ge=0, le=50)
    content_type: str = Field(default="video", max_length=32)


class DouyinSelectionCompletePayload(BaseModel):
    items: list[DouyinSelectionVideoPayload] = Field(min_length=1, max_length=50)


class DouyinSelectionPublishItemPayload(BaseModel):
    video_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    caption: str = Field(min_length=1, max_length=10000)
    scheduled_at: str = Field(default="", max_length=64)


class DouyinSelectionPublishPayload(BaseModel):
    batch_name: str = Field(default="", max_length=160)
    items: list[DouyinSelectionPublishItemPayload] = Field(min_length=1, max_length=50)
    targets: list[ManualPublishTargetPayload] = Field(min_length=1, max_length=50)


class PublisherReadyCheckPayload(BaseModel):
    targets: list[ManualPublishTargetPayload] = Field(min_length=1, max_length=50)
    force: bool = True


class AssistantMessagePayload(BaseModel):
    role: str = Field(pattern=r"^(user|assistant)$")
    content: str = Field(min_length=1, max_length=12000)


class AssistantChatPayload(BaseModel):
    messages: list[AssistantMessagePayload] = Field(min_length=1, max_length=20)


class AssistantConfirmPayload(BaseModel):
    proposal_token: str = Field(min_length=20, max_length=256)


class AssistantCaptionPayload(BaseModel):
    original_description: str = Field(default="", max_length=10000)
    instruction: str = Field(default="", max_length=4000)
    video_label: str = Field(default="", max_length=512)


class VideoAiProjectCreatePayload(BaseModel):
    source_path: str = Field(min_length=1, max_length=4096)


class VideoAiTranscribePayload(BaseModel):
    source_language: str = Field(default="auto", min_length=1, max_length=32)
    model_name: str = Field(default="small", min_length=1, max_length=32)


class VideoAiSubtitleDetectionPayload(BaseModel):
    sample_count: int = Field(default=24, ge=8, le=48)


class VideoAiTranslatePayload(BaseModel):
    target_language: str = Field(min_length=1, max_length=32)


class VideoAiSubtitlePayload(BaseModel):
    id: str = Field(default="", max_length=64)
    start: float = Field(ge=0, le=864000)
    end: float = Field(gt=0, le=864000)
    text: str = Field(default="", max_length=12000)
    translated_text: str = Field(default="", max_length=12000)


class VideoAiSubtitlesSavePayload(BaseModel):
    subtitles: list[VideoAiSubtitlePayload] = Field(default_factory=list, max_length=5000)


class VideoAiBlurPayload(BaseModel):
    enabled: bool = False
    x: float = Field(default=0.05, ge=0, le=1)
    y: float = Field(default=0.78, ge=0, le=1)
    width: float = Field(default=0.90, gt=0, le=1)
    height: float = Field(default=0.17, gt=0, le=1)
    strength: int = Field(default=22, ge=2, le=60)


class VideoAiStylePayload(BaseModel):
    font_name: str = Field(default="Arial", min_length=1, max_length=80)
    font_size: int = Field(default=42, ge=12, le=300)
    margin_v: int = Field(default=54, ge=0, le=500)
    outline: float = Field(default=2, ge=0, le=8)
    primary_color: str = Field(default="&H00FFFFFF", max_length=16)


class VideoAiEditorSavePayload(BaseModel):
    subtitles: list[VideoAiSubtitlePayload] = Field(default_factory=list, max_length=5000)
    blur: VideoAiBlurPayload = Field(default_factory=VideoAiBlurPayload)
    style: VideoAiStylePayload = Field(default_factory=VideoAiStylePayload)
    dubbing: "VideoAiDubbingPayload | None" = None


class VideoAiDubbingPayload(BaseModel):
    enabled: bool = False
    provider: str = Field(default="edge", pattern=r"^(edge|vieneu)$")
    voice: str = Field(default="vi-VN-HoaiMyNeural", min_length=1, max_length=120)
    style: str = Field(default="tu_nhien", pattern=r"^(tu_nhien|tin_tuc|doc_truyen)$")
    rate: int = Field(default=0, ge=-50, le=50)
    volume: int = Field(default=100, ge=0, le=200)
    original_volume: int | None = Field(default=None, ge=0, le=100)


class VideoAiTtsPreparePayload(BaseModel):
    provider: str = Field(default="vieneu", pattern=r"^(edge|vieneu)$")


class VideoAiTtsPreviewPayload(BaseModel):
    provider: str = Field(default="vieneu", pattern=r"^(edge|vieneu)$")
    voice: str = Field(min_length=1, max_length=120)
    style: str = Field(default="tu_nhien", pattern=r"^(tu_nhien|tin_tuc|doc_truyen)$")
    text: str = Field(default="", max_length=300)


class VideoAiRenderPayload(BaseModel):
    track: str = Field(default="translated", pattern=r"^(source|translated)$")
    blur: VideoAiBlurPayload = Field(default_factory=VideoAiBlurPayload)
    style: VideoAiStylePayload = Field(default_factory=VideoAiStylePayload)
    dubbing: "VideoAiDubbingPayload | None" = None
    output_path: str = Field(default="", max_length=4096)
