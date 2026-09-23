"""config.yaml/.env 加载，密钥与业务配置分离（FR-CFG-01，SDD §6）。

密钥（api_id/api_hash/bot_token/proxy/web_login_secret/web_host/web_port）与
部署路径（save_directory/session_directory/temp_directory）只在 config.yaml 或
环境变量；业务配置入库 settings 表。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "TGM_"


def _env(key: str) -> str | None:
    return os.environ.get(f"{ENV_PREFIX}{key.upper()}")


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
    proxy: ProxyConfig | None = None

    @property
    def has_credentials(self) -> bool:
        return self.api_id != 0 and bool(self.api_hash)


def _load_config_file(base_dir: Path) -> dict[str, Any]:
    """读取 base_dir/config.yaml；缺失或非映射时返回空字典。"""
    path = base_dir / "config.yaml"
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


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
    """从 base_dir/config.yaml 与环境变量装配密钥；env 覆盖文件。"""
    cfg = secrets_from_data(_load_config_file(base_dir))
    cfg.api_id = int(_env("api_id") or cfg.api_id)
    cfg.api_hash = _env("api_hash") or cfg.api_hash
    cfg.bot_token = _env("bot_token") or cfg.bot_token
    cfg.web_host = _env("web_host") or cfg.web_host
    cfg.web_port = int(_env("web_port") or cfg.web_port)
    cfg.web_login_secret = _env("web_login_secret") or cfg.web_login_secret
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
    session_directory: str = "sessions"
    temp_directory: str = "temp"


def load_path_config(base_dir: Path) -> PathConfig:
    """路径布局：config.yaml 的 save/session/temp_directory，同名 TGM_* env 覆盖。"""
    data = _load_config_file(base_dir)
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

    save/session/temp 三目录可由 config.yaml 或同名 TGM_* env 覆盖为绝对路径
    （如 /data/downloads、/data/sessions、/data/temp）；preview 跟随 temp，
    data/logs 固定相对 base_dir。
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
        "logs": root / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs
