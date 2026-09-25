"""统一配置管理（SRP/DIP）：运行配置单一来源。

服务装配所需的全部配置一次性解析为不可变 ``AppSettings``：
- 密钥/代理/端口来自 ``SecretConfig``（config.yaml + TGM_* env，FR-CFG-01）；
- 业务配置来自 settings 表（DB，FR-CFG-03），缺省值集中在此一处。

``__main__.build_services``、``run.py`` 与测试共用 ``load_app_settings``，
各服务的缺省值不再散落在装配代码里。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import SecretConfig, load_secrets
from app.ports.repository import SettingsRepo

# ---- 业务配置缺省值（原散落在 __main__ 装配代码里的字面量收口于此）----

DEFAULT_DIR_TEMPLATE = "{artist}/{album}"
DEFAULT_FILE_TEMPLATE = "{track:02d} {title}"
DEFAULT_DATE_FORMAT = "%Y-%m"
DEFAULT_MAX_DOWNLOAD_TASK = 3
DEFAULT_PREVIEW_CACHE_MAX_BYTES = 512 * 1024 * 1024
def _int_setting(store: SettingsRepo, key: str, default: int) -> int:
    raw = store.get_setting(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _str_setting(store: SettingsRepo, key: str, default: str) -> str:
    return store.get_setting(key) or default


@dataclass(slots=True, frozen=True)
class TemplateSettings:
    """落盘模板配置（FR-NAME-01）。"""

    dir_template: str
    file_template: str
    date_format: str
    save_path: Path


@dataclass(slots=True, frozen=True)
class DownloadSettings:
    """下载队列配置（FR-DL-01）。"""

    max_concurrent: int


@dataclass(slots=True, frozen=True)
class PreviewSettings:
    """试听缓存配置（FR-PLAY-01）。"""

    max_bytes: int


@dataclass(slots=True, frozen=True)
class AppSettings:
    """应用装配配置聚合（不可变）。"""

    secrets: SecretConfig
    template: TemplateSettings
    download: DownloadSettings
    preview: PreviewSettings


def load_app_settings(
    base_dir: Path, store: SettingsRepo, *, secrets: SecretConfig | None = None
) -> AppSettings:
    """装配配置：密钥 + DB settings（缺省值集中管理，SRP）。"""
    secret_cfg = secrets if secrets is not None else load_secrets(base_dir)
    return AppSettings(
        secrets=secret_cfg,
        template=TemplateSettings(
            dir_template=_str_setting(store, "dir_template", DEFAULT_DIR_TEMPLATE),
            file_template=_str_setting(store, "file_template", DEFAULT_FILE_TEMPLATE),
            date_format=_str_setting(store, "date_format", DEFAULT_DATE_FORMAT),
            # 空 save_path 回退 base_dir/downloads：不能用相对 '.'（当前目录）——
            # 服务从别的目录启动时（workdir ≠ base_dir），文件会落到启动目录而不是曲库。
            save_path=Path(store.get_setting("save_path") or base_dir / "downloads"),
        ),
        download=DownloadSettings(
            max_concurrent=_int_setting(store, "max_download_task", DEFAULT_MAX_DOWNLOAD_TASK),
        ),
        preview=PreviewSettings(
            max_bytes=_int_setting(
                store, "preview_cache_max_bytes", DEFAULT_PREVIEW_CACHE_MAX_BYTES
            ),
        ),
    )
