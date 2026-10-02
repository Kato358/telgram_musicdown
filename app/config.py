"""config.yaml/.env 加载，密钥与业务配置分离（FR-CFG-01，SDD §6）。

密钥（api_id/api_hash/bot_token/proxy/web_login_secret/web_host/web_port）与
部署路径（save_directory/session_directory/temp_directory）只在 config.yaml 或
环境变量；业务配置入库 settings 表。文件本身落在 ``<base_dir>/data/config.yaml``
（Docker 即宿主机挂出来的 ``./data/config.yaml``），旧位置 ``<base_dir>/config.yaml``
首次启动自动迁移。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "TGM_"

# 密钥文件位置：<base_dir>/data/config.yaml（FR-CFG-01）。刻意收在 data/ 里——那是部署时
# 唯一必须挂出来的状态目录（SQLite、会话、日志都在它下面），密钥于是跟着一起持久化，
# 不必为单个文件再挂一次 bind mount，也就没有「宿主文件忘建，Docker 把它建成目录」那种坑。
CONFIG_DIR = "data"
CONFIG_FILE_NAME = "config.yaml"

logger = logging.getLogger(__name__)

# 旧位置提示只打一次：读路径被 settings/registry 等按请求触发，日志不能被它刷屏。
_legacy_read_warned = False

# ChKSz 在线源的默认服务地址（可自建部署，用 TGM_CHKSZ_BASE_URL 或 config.yaml 覆盖）。
DEFAULT_CHKSZ_BASE_URL = "https://api.chksz.com"


def config_file(base_dir: Path) -> Path:
    """密钥文件的唯一事实源位置：<base_dir>/data/config.yaml。"""
    return base_dir / CONFIG_DIR / CONFIG_FILE_NAME


def _legacy_config_file(base_dir: Path) -> Path:
    """v0.17 前的位置 <base_dir>/config.yaml；只读兜底与一次性迁移用，不再写。"""
    return base_dir / CONFIG_FILE_NAME


def load_config_data(base_dir: Path) -> dict[str, Any]:
    """读取密钥文件；缺失或非映射时返回空字典。

    新位置没有而旧位置有 → 读旧位置并提示一次（`migrate_legacy_config` 会把它搬过去；
    兜底覆盖没走 ``main()`` 的调用方：测试、直接装配 build_services）。
    """
    path = config_file(base_dir)
    if not path.is_file():
        legacy = _legacy_config_file(base_dir)
        if not legacy.is_file():
            return {}
        global _legacy_read_warned  # noqa: PLW0603  只为一处「提示一次」的开关
        if not _legacy_read_warned:
            _legacy_read_warned = True
            logger.warning("config.yaml 在旧位置 %s 生效，目标位置是 %s", legacy, path)
        path = legacy
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def migrate_legacy_config(base_dir: Path) -> Path | None:
    """把旧位置的 config.yaml 搬进 data/（一次性，尽力而为；已迁移或不存在返回 None）。

    搬而不是拷：两个位置并存会让「向导到底写哪个」变成猜谜，留下一个被忽略的文件就是
    下一次「改了密钥怎么不生效」的源头。跨挂载点（旧位置被人手动 bind mount 过）会
    ``EXDEV``，此时只告警——读取兜底仍在，向导保存也会写到新位置。
    """
    legacy, target = _legacy_config_file(base_dir), config_file(base_dir)
    if not legacy.exists() or target.exists():
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(target)
    except OSError:
        logger.warning(
            "迁移 config.yaml 失败：%s 与 %s 可能不在同一挂载点，请删掉旧位置的手动挂载后重启",
            legacy,
            target,
            exc_info=True,
        )
        return None
    logger.info("config.yaml 已迁移：%s → %s", legacy, target)
    return target


def write_config_data(base_dir: Path, data: dict[str, Any]) -> Path:
    """写回密钥文件；父目录按需建——向导首次保存即完成「自动创建」（FR-OPS-02）。"""
    path = config_file(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _env(key: str) -> str | None:
    return os.environ.get(f"{ENV_PREFIX}{key.upper()}")


# 布尔开关（web_login_enabled）认得的写法；表里没有的值一律按 default 处理
# （安全向：拼写错误解析失败时宁可保持开启登录，也不能把保护静默关掉）。
_BOOL_MAP = {
    "1": True,
    "true": True,
    "yes": True,
    "on": True,
    "0": False,
    "false": False,
    "no": False,
    "off": False,
}


def _as_bool(value: Any, default: bool) -> bool:
    """宽松布尔解析：bool/0-1 原生收下，字符串认常见写法，认不出按 default。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if value is None:
        return default
    mapped = _BOOL_MAP.get(str(value).strip().lower())
    return default if mapped is None else mapped


def _env_bool(key: str) -> bool | None:
    """环境变量里的布尔开关；未设/留空返回 None（= 不覆盖，沿用配置文件值）。"""
    raw = _env(key)
    if raw is None or not raw.strip():
        return None
    return _BOOL_MAP.get(raw.strip().lower())


@dataclass(slots=True)
class ProxyConfig:
    """SOCKS5/HTTP 代理（FR-AUTH-03），User 与 Bot 共用。"""

    scheme: str = "socks5"
    hostname: str = "127.0.0.1"
    port: int = 1080
    username: str | None = None
    password: str | None = None


@dataclass(slots=True)
class SecretConfig:
    """密钥与端口段；不进 git、不入库（NFR-02）。"""

    api_id: int = 0
    api_hash: str = ""
    bot_token: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_login_secret: str = ""
    # 控制台登录总开关（FR-WEB-02）：False = 显式关闭登录（含 0.0.0.0 部署），
    # 用户自行承担暴露面；缺省 True 保持「有口令或非本机绑定即要求登录」的原判据。
    web_login_enabled: bool = True
    proxy: ProxyConfig | None = None

    # ChKSz 在线源密钥（SDD §2.7）：与 Telegram 密钥同段——只进 config.yaml/.env，
    # 不入 settings 表（那张表的内容 GET /api/settings 原样返回给浏览器）。
    chksz_api_key: str = ""
    chksz_base_url: str = DEFAULT_CHKSZ_BASE_URL

    @property
    def has_credentials(self) -> bool:
        return self.api_id != 0 and bool(self.api_hash)


def secrets_from_data(data: dict[str, Any]) -> SecretConfig:
    """config.yaml 内容 → SecretConfig（不含环境变量覆盖）。

    供 load_secrets 与向导的「写盘前校验」共用：校验必须看即将落盘的内容，
    而不是当前进程里可能被 TGM_* 覆盖过的值。
    """
    cfg = SecretConfig()
    cfg.api_id = int(data.get("api_id") or 0)
    cfg.api_hash = str(data.get("api_hash") or "")
    cfg.bot_token = str(data.get("bot_token") or "")
    cfg.web_host = str(data.get("web_host") or "127.0.0.1")
    cfg.web_port = int(data.get("web_port") or 8787)
    cfg.web_login_secret = str(data.get("web_login_secret") or "")
    cfg.web_login_enabled = _as_bool(data.get("web_login_enabled"), True)
    cfg.chksz_api_key = str(data.get("chksz_api_key") or "")
    cfg.chksz_base_url = str(data.get("chksz_base_url") or DEFAULT_CHKSZ_BASE_URL).rstrip("/")
    proxy_raw = data.get("proxy")
    if isinstance(proxy_raw, dict):
        cfg.proxy = ProxyConfig(
            scheme=str(proxy_raw.get("scheme", "socks5")),
            hostname=str(proxy_raw.get("hostname", "127.0.0.1")),
            port=int(proxy_raw.get("port", 1080)),
            username=proxy_raw.get("username"),
            password=proxy_raw.get("password"),
        )
    return cfg


def load_secrets(base_dir: Path) -> SecretConfig:
    """从 data/config.yaml 与环境变量装配密钥；env 覆盖文件。"""
    cfg = secrets_from_data(load_config_data(base_dir))
    cfg.api_id = int(_env("api_id") or cfg.api_id)
    cfg.api_hash = _env("api_hash") or cfg.api_hash
    cfg.bot_token = _env("bot_token") or cfg.bot_token
    cfg.web_host = _env("web_host") or cfg.web_host
    cfg.web_port = int(_env("web_port") or cfg.web_port)
    cfg.web_login_secret = _env("web_login_secret") or cfg.web_login_secret
    cfg.chksz_api_key = _env("chksz_api_key") or cfg.chksz_api_key
    cfg.chksz_base_url = (_env("chksz_base_url") or cfg.chksz_base_url).rstrip("/")
    enabled_env = _env_bool("web_login_enabled")
    if enabled_env is not None:
        cfg.web_login_enabled = enabled_env
    if _env("proxy_host"):
        cfg.proxy = ProxyConfig(
            hostname=_env("proxy_host") or "127.0.0.1",
            port=int(_env("proxy_port") or 1080),
        )
    return cfg


@dataclass(slots=True)
class PathConfig:
    """部署路径布局（FR-CFG-02）；相对值以 TGM_BASE_DIR 为基准，绝对路径原样使用。"""

    save_directory: str = "downloads"
    session_directory: str = "data/sessions"
    temp_directory: str = "data/temp"


def load_path_config(base_dir: Path) -> PathConfig:
    """路径布局：config.yaml 的 save/session/temp_directory，同名 TGM_* env 覆盖。"""
    data = load_config_data(base_dir)
    cfg = PathConfig()
    for field in ("save_directory", "session_directory", "temp_directory"):
        value = _env(field) or data.get(field)
        if value:
            setattr(cfg, field, str(value))
    return cfg


def resolve_dir(value: str, root: Path) -> Path:
    """解析目录配置：相对值相对 root，绝对路径原样使用；返回值恒为绝对路径。

    Windows 上 ``/data/downloads`` 这类无盘符的盘根写法锚定到 root 所在盘
    （``C:\\data\\downloads``），避免随进程当前盘漂移。
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if path.anchor:  # 仅 Windows：形如 "\data\downloads" 的盘根相对路径
        drive = root.drive or Path.cwd().drive
        if drive:
            return Path(drive + str(path))
        return Path(str(root.anchor) + str(path).lstrip("\\/"))
    return (root / path).absolute()


def app_dirs(base_dir: Path | None = None, paths: PathConfig | None = None) -> dict[str, Path]:
    """FR-CFG-02 路径布局；目录按需创建。

    save 可由 config.yaml 或 TGM_SAVE_DIRECTORY 覆盖为绝对路径（如 /data/downloads）；
    sessions/temp/logs 默认收在 data/ 下（data/sessions、data/temp、data/logs），
    sessions/temp 也可由 config.yaml 或同名 TGM_* env 覆盖为绝对路径；preview 跟随 temp。
    """
    root = base_dir or Path.cwd()
    cfg = paths or load_path_config(root)
    temp = resolve_dir(cfg.temp_directory, root)
    dirs = {
        "root": root,
        "save_path": resolve_dir(cfg.save_directory, root),
        "temp": temp,
        "preview": temp / "preview",
        "sessions": resolve_dir(cfg.session_directory, root),
        "data": root / "data",
        "logs": root / "data" / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def web_dist_dir() -> Path:
    """前端构建产物目录：跟随**代码**位置，不跟随 TGM_BASE_DIR。

    Docker 里 ``TGM_BASE_DIR=/data`` 而代码在 ``/opt/app``（产物 ``/opt/app/web/dist``）；
    拿数据目录拼路径会让首次部署打开 Web 只拿到 404，初始化向导因此不可达。
    需要放到别处时用 ``TGM_STATIC_DIR`` 覆盖。
    """
    override = _env("static_dir")
    if override:
        return Path(override).expanduser().absolute()
    return Path(__file__).resolve().parent.parent / "web" / "dist"
