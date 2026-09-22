"""首次部署初始化（FR-OPS-02）：Web 内保存密钥并写入 config.yaml。

- 密钥不进 git、不入库（NFR-02）：保存到 config.yaml，env 仍可覆盖。
- 保存后提示重启；api_id/proxy/bot_token 支持重启后生效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.config import SecretConfig, load_secrets


def save_secrets(base_dir: Path, values: dict[str, Any]) -> SecretConfig:
    """把初始化表单的密钥合并写入 config.yaml；返回保存后的 SecretConfig。

    允许的键：api_id, api_hash, bot_token, web_login_secret,
    proxy: {scheme, hostname, port, username?, password?}
    """
    path = base_dir / "config.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    for key in ("api_id", "api_hash", "bot_token", "web_login_secret"):
        if key in values and values[key] not in (None, ""):
            data[key] = values[key]
    proxy = values.get("proxy")
    if isinstance(proxy, dict) and proxy.get("hostname"):
        data["proxy"] = {
            "scheme": str(proxy.get("scheme", "socks5")),
            "hostname": str(proxy["hostname"]),
            "port": int(proxy.get("port", 1080)),
            **({"username": proxy["username"]} if proxy.get("username") else {}),
            **({"password": proxy["password"]} if proxy.get("password") else {}),
        }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return load_secrets(base_dir)


def setup_complete(secrets: SecretConfig) -> bool:
    """初始化完成判定：凭据齐备。bot_token 可选。"""
    return secrets.has_credentials
