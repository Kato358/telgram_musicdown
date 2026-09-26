"""统一配置管理（SRP/DIP）：运行配置单一来源。

服务装配所需的全部配置一次性解析为不可变 ``AppSettings``：
- 密钥/代理/端口来自 ``SecretConfig``（config.yaml + TGM_* env，FR-CFG-01）；
- 业务配置来自 settings 表（DB，FR-CFG-03），缺省值集中在此一处。

``__main__.build_services``、``run.py`` 与测试共用 ``load_app_settings``，
各服务的缺省值不再散落在装配代码里。设置页保存后的即时刷新走
``load_template_config``/``preview_max_bytes``（只读 settings 表，不重读密钥文件）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config import SecretConfig, load_secrets
from app.domain import TemplateConfig
from app.ports.repository import SettingsRepo

# ---- 业务配置缺省值（原散落在 __main__ 装配代码里的字面量收口于此）----

DEFAULT_DIR_TEMPLATE = "{artist}/{album}"
DEFAULT_FILE_TEMPLATE = "{track:02d} {title}"
DEFAULT_DATE_FORMAT = "%Y-%m"
DEFAULT_MAX_DOWNLOAD_TASK = 3
DEFAULT_PREVIEW_CACHE_MAX_BYTES = 512 * 1024 * 1024

# ---- 搜索（FR-SEARCH-01/03，SDD §2.6）----
# TTL 按「频道里新歌入库不频繁」定：15 分钟内同词同源直接命中，避免重复打 Telegram。
DEFAULT_SEARCH_CACHE_TTL_SEC = 900
# 空结果只留 L1 短负缓存（不落 L2）：挡同词突发重打，又不至于让「刚发的歌」长期查不到。
DEFAULT_SEARCH_CACHE_NEGATIVE_TTL_SEC = 60
DEFAULT_SEARCH_CACHE_MAX_ENTRIES = 512
DEFAULT_SEARCH_CACHE_MAX_BYTES = 32 * 1024 * 1024
# 并发扇出上限：交互式搜索要的是「别把账号打进 FloodWait」，不是吞吐越大越好。
DEFAULT_SEARCH_FANOUT = 4
# 同步窗口：窗口内回来的源直接进响应，超窗的转后台补齐（下次同词命中完整结果）。
DEFAULT_SEARCH_SYNC_WINDOW_MS = 4000
DEFAULT_SEARCH_PAGE_SIZE = 20
SEARCH_MAX_PAGE_SIZE = 100


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


def load_template_config(store: SettingsRepo, base_dir: Path) -> TemplateConfig:
    """落盘模板配置（FR-NAME-01）：settings 表现读，容器装配与保存后即时刷新共用。

    目录模板允许保存空值（空 = 不建子目录，文件直接落根目录），只有缺键才回退默认；
    文件名模板没有「空」语义（文件总得有名字），空值一律回退默认。
    """
    raw_dir = store.get_setting("dir_template")
    return TemplateConfig(
        dir_template=raw_dir if raw_dir is not None else DEFAULT_DIR_TEMPLATE,
        file_template=_str_setting(store, "file_template", DEFAULT_FILE_TEMPLATE),
        date_format=_str_setting(store, "date_format", DEFAULT_DATE_FORMAT),
        # 空 save_path 回退 base_dir/downloads：不能用相对 '.'（当前目录）——
        # 服务从别的目录启动时（workdir ≠ base_dir），文件会落到启动目录而不是曲库。
        save_path=Path(store.get_setting("save_path") or base_dir / "downloads"),
    )


def preview_max_bytes(store: SettingsRepo) -> int:
    """试听缓存上限（FR-PLAY-01）：设置页保存后即时刷进 PreviewService。"""
    return _int_setting(store, "preview_cache_max_bytes", DEFAULT_PREVIEW_CACHE_MAX_BYTES)


@dataclass(slots=True, frozen=True)
class DownloadSettings:
    """下载队列配置（FR-DL-01）。"""

    max_concurrent: int


@dataclass(slots=True, frozen=True)
class PreviewSettings:
    """试听缓存配置（FR-PLAY-01）。"""

    max_bytes: int


@dataclass(slots=True, frozen=True)
class SearchCacheSettings:
    """搜索二级缓存的调参（SDD §2.6）：TTL、负缓存与容量。"""

    ttl_sec: int = DEFAULT_SEARCH_CACHE_TTL_SEC
    negative_ttl_sec: int = DEFAULT_SEARCH_CACHE_NEGATIVE_TTL_SEC
    max_entries: int = DEFAULT_SEARCH_CACHE_MAX_ENTRIES
    max_bytes: int = DEFAULT_SEARCH_CACHE_MAX_BYTES


@dataclass(slots=True, frozen=True)
class SearchSettings:
    """搜索运行配置（FR-SEARCH-01/03）：缓存 + 扇出 + 同步窗口 + 分页。

    字段都有缺省值：测试与「无 store 场景」直接 ``SearchSettings()`` 即可，
    装配路径走 ``load_search_settings(store)`` 让 settings 表覆盖。
    """

    cache: SearchCacheSettings = field(default_factory=SearchCacheSettings)
    fanout: int = DEFAULT_SEARCH_FANOUT
    sync_window_sec: float = DEFAULT_SEARCH_SYNC_WINDOW_MS / 1000
    page_size: int = DEFAULT_SEARCH_PAGE_SIZE
    max_page_size: int = SEARCH_MAX_PAGE_SIZE


def load_search_settings(store: SettingsRepo) -> SearchSettings:
    """搜索配置（settings 表现读）；装配时用缺省值，保存设置后由容器刷新。"""
    return SearchSettings(
        cache=SearchCacheSettings(
            ttl_sec=_int_setting(store, "search_cache_ttl_sec", DEFAULT_SEARCH_CACHE_TTL_SEC),
            negative_ttl_sec=_int_setting(
                store, "search_cache_negative_ttl_sec", DEFAULT_SEARCH_CACHE_NEGATIVE_TTL_SEC
            ),
            max_entries=_int_setting(
                store, "search_cache_max_entries", DEFAULT_SEARCH_CACHE_MAX_ENTRIES
            ),
            max_bytes=_int_setting(
                store, "search_cache_max_bytes", DEFAULT_SEARCH_CACHE_MAX_BYTES
            ),
        ),
        fanout=max(1, _int_setting(store, "search_fanout", DEFAULT_SEARCH_FANOUT)),
        sync_window_sec=max(
            0, _int_setting(store, "search_sync_window_ms", DEFAULT_SEARCH_SYNC_WINDOW_MS)
        )
        / 1000,
        page_size=max(1, _int_setting(store, "search_page_size", DEFAULT_SEARCH_PAGE_SIZE)),
        max_page_size=SEARCH_MAX_PAGE_SIZE,
    )


@dataclass(slots=True, frozen=True)
class AppSettings:
    """应用装配配置聚合（不可变）。"""

    secrets: SecretConfig
    template: TemplateConfig
    download: DownloadSettings
    preview: PreviewSettings
    search: SearchSettings


def load_app_settings(
    base_dir: Path, store: SettingsRepo, *, secrets: SecretConfig | None = None
) -> AppSettings:
    """装配配置：密钥 + DB settings（缺省值集中管理，SRP）。"""
    secret_cfg = secrets if secrets is not None else load_secrets(base_dir)
    return AppSettings(
        secrets=secret_cfg,
        template=load_template_config(store, base_dir),
        download=DownloadSettings(
            max_concurrent=_int_setting(store, "max_download_task", DEFAULT_MAX_DOWNLOAD_TASK),
        ),
        preview=PreviewSettings(max_bytes=preview_max_bytes(store)),
        search=load_search_settings(store),
    )
