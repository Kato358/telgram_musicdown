"""API 请求/响应 Pydantic schema（编码规范 §2.2：禁止裸 dict 进出路由）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SendCodeRequest(BaseModel):
    phone: str


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


class SetupStatusResponse(BaseModel):
    """初始化状态（FR-OPS-02）：放行 = 密钥齐备 + 已登录；音乐源可选。

    proxy 只回协议/地址/端口，不回用户名密码（NFR-02）。
    """

    complete: bool
    has_api_id: bool
    has_api_hash: bool
    has_bot_token: bool
    proxy: ProxySpec | None = None
    connected: bool = False
    display_name: str | None = None
    username: str | None = None


class SendCodeResponse(BaseModel):
    """发码结果：已有有效会话时 authorized=True 且不填 code_hash。"""

    code_hash: str
    authorized: bool = False


class MeResponse(BaseModel):
    display_name: str | None = None
    username: str | None = None
    premium: bool = False
    connected: bool = False


class TaskCounts(BaseModel):
    queued: int = 0
    downloading: int = 0
    paused: int = 0
    failed: int = 0
    success: int = 0


class LibraryCounts(BaseModel):
    """曲库计数：`tracks` 只算已写盘的行（save_path 非空）。

    `bytes` 是这些文件在磁盘上的实际大小之和——刻意不取 `history.file_size` 求和：
    链接/转发入队时 Telegram 经常不给大小，那样求和会让「占用」长期停在 0。
    磁盘才是这个数字的事实源（文件被移走就不算占用）。
    """

    tracks: int = 0
    bytes: int = 0
    failed: int = 0


class SourceCounts(BaseModel):
    total: int = 0
    enabled: int = 0


class StatsResponse(BaseModel):
    """控制台统计（`GET /api/stats`）：统计卡与系统状态一次取全，前端不再自己数列表。"""

    tasks: TaskCounts
    library: LibraryCounts
    sources: SourceCounts
    uptime_sec: float = 0.0


class LogoutResponse(BaseModel):
    """退出登录（FR-AUTH-02）：回被删的会话文件名，界面据此说明「下次要重新输码」。"""

    ok: bool = True
    removed_sessions: list[str] = Field(default_factory=list)


class ResetResponse(BaseModel):
    """重新执行初始化（FR-OPS-02）：清掉的 config.yaml 键 + 删除的会话文件。"""

    ok: bool = True
    cleared_keys: list[str] = Field(default_factory=list)
    removed_sessions: list[str] = Field(default_factory=list)


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
