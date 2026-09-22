"""API 请求/响应 Pydantic schema（编码规范 §2.2：禁止裸 dict 进出路由）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SendCodeRequest(BaseModel):
    phone: str


class SendCodeResponse(BaseModel):
    code_hash: str


class SignInRequest(BaseModel):
    phone: str
    code: str
    code_hash: str
    password: str | None = None

class ProxySpec(BaseModel):
    scheme: str = "socks5"
    hostname: str
    port: int = 1080
    username: str | None = None
    password: str | None = None


class SetupSecretsRequest(BaseModel):
    api_id: int | None = None
    api_hash: str | None = None
    bot_token: str | None = None
    web_login_secret: str | None = None
    proxy: ProxySpec | None = None


class MeResponse(BaseModel):
    display_name: str | None = None
    username: str | None = None
    premium: bool = False
    connected: bool = False


class SourceUpsertRequest(BaseModel):
    link: str
    enabled: bool = True
    auto_sync: bool = False
    sync_interval_sec: int = 120
    media_scope: list[str] = Field(default_factory=lambda: ["audio", "audio_document"])
    filters: dict[str, Any] | None = None
    save_path_override: str | None = None
    dir_template_override: str | None = None
    file_template_override: str | None = None
    note: str | None = None


class SourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    auto_sync: bool | None = None
    sync_interval_sec: int | None = None
    media_scope: list[str] | None = None
    filters: dict[str, Any] | None = None
    save_path_override: str | None = None
    dir_template_override: str | None = None
    file_template_override: str | None = None
    note: str | None = None


class BackfillRequest(BaseModel):
    direction: str = "backward"
    to_message_id: int | None = None
    to_date: str | None = None
    limit: int | None = None


class SearchRequest(BaseModel):
    q: str
    source_ids: list[int] | None = None
    fields: list[str] | None = None
    filters: dict[str, Any] | None = None
    sort: str = "relevance"
    page: int = 0


class DownloadsRequest(BaseModel):
    urls: list[str] | None = None
    message_refs: list[dict[str, Any]] | None = None
    force: bool = False


class PreviewPathRequest(BaseModel):
    dir_template: str
    file_template: str
    sample: dict[str, Any] | None = None


class SettingsUpdateRequest(BaseModel):
    values: dict[str, Any]
