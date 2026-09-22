"""config.yaml/.env 加载，密钥与业务配置分离（FR-CFG-01，SDD §6）。

密钥（api_id/api_hash/bot_token/proxy/web_login_secret/web_host/web_port）
只在 config.yaml 或环境变量；业务配置入库 settings 表。
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


def load_secrets(base_dir: Path) -> SecretConfig:
    """从 base_dir/config.yaml 与环境变量装配密钥；env 覆盖文件。"""
    cfg = SecretConfig()
    path = base_dir / "config.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    cfg.api_id = int(_env("api_id") or data.get("api_id") or 0)
    cfg.api_hash = str(_env("api_hash") or data.get("api_hash") or "")
    cfg.bot_token = str(_env("bot_token") or data.get("bot_token") or "")
    cfg.web_host = str(_env("web_host") or data.get("web_host") or "127.0.0.1")
    cfg.web_port = int(_env("web_port") or data.get("web_port") or 8787)
    cfg.web_login_secret = str(_env("web_login_secret") or data.get("web_login_secret") or "")
    proxy_raw = data.get("proxy")
    if isinstance(proxy_raw, dict):
        cfg.proxy = ProxyConfig(
            scheme=str(proxy_raw.get("scheme", "socks5")),
            hostname=str(proxy_raw.get("hostname", "127.0.0.1")),
            port=int(proxy_raw.get("port", 1080)),
            username=proxy_raw.get("username"),
            password=proxy_raw.get("password"),
        )
    if _env("proxy_host"):
        cfg.proxy = ProxyConfig(
            hostname=_env("proxy_host") or "127.0.0.1",
            port=int(_env("proxy_port") or 1080),
        )
    return cfg


def app_dirs(base_dir: Path | None = None) -> dict[str, Path]:
    """FR-CFG-02 路径布局；目录按需创建。"""
    root = base_dir or Path.cwd()
    dirs = {
        "root": root,
        "save_path": root / "library",
        "temp": root / "temp",
        "preview": root / "temp" / "preview",
        "sessions": root / "sessions",
        "data": root / "data",
        "logs": root / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs
