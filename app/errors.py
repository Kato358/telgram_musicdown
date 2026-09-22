"""自定义异常层次：AppError 基类 + 按域子类（编码规范 §2.4）。

FastAPI 全局 handler 统一转 SDD §4.1 错误包络：
``{"error": {"code": "...", "message": "人类可读原因"}}``
"""

from __future__ import annotations


class AppError(Exception):
    """业务异常基类：code 进错误包络，message 人类可读（NFR-08）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LinkParseError(AppError):
    """链接解析失败（FR-LINK-01），不入队。"""


class SourceUnreachableError(AppError):
    """源不可达；reason: not_joined|banned|invalid_link|not_chat（FR-SRC-01）。"""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__("source_unreachable", message or f"source unreachable: {reason}")
        self.reason = reason


class NotAudioError(AppError):
    """目标消息不是音频（验收 #11）。"""

    def __init__(self, message: str = "message is not audio") -> None:
        super().__init__("not_audio", message)


class TagWriteError(AppError):
    """标签写失败（NFR-10，文件不受损）。"""

    def __init__(self, message: str, reason: str = "tag_write_failed") -> None:
        super().__init__(reason, message)


class UnsupportedContainerError(AppError):
    """只读/不支持容器（cue/ape/wma 等，FR-TAG-02）。"""

    def __init__(self, message: str = "unsupported container") -> None:
        super().__init__("unsupported_container", message)


class AuthRequiredError(AppError):
    """无 Web 会话访问受保护资源（验收 #16）。"""

    def __init__(self, message: str = "authentication required") -> None:
        super().__init__("unauthorized", message)


class WebAuthConfigError(AppError):
    """0.0.0.0 无密码启动拒绝（FR-WEB-02）。"""

    def __init__(self, message: str = "web_login_secret required when binding 0.0.0.0") -> None:
        super().__init__("web_auth_config", message)


class TaskNotFoundError(AppError):
    """任务/历史 id 不存在。"""

    def __init__(self, message: str = "task not found") -> None:
        super().__init__("not_found", message)


class SessionLockedError(AppError):
    """会话文件被另一个运行实例占用（sqlite database is locked）。"""

    def __init__(self, message: str = "session file locked by another running instance") -> None:
        super().__init__("session_locked", message)
