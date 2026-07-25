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


class LoginPayload(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterPayload(BaseModel):
    phone: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)


class CreateOrderPayload(BaseModel):
    days: int


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
    file_paths: list[str] = Field(default_factory=list, max_length=20)
    caption: str = Field(default="", max_length=10000)
    items: list[ManualPublishItemPayload] = Field(default_factory=list, max_length=20)
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
