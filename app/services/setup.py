"""首次部署初始化（FR-OPS-02）：Web 内保存密钥并写入 config.yaml。

- 密钥不进 git、不入库（NFR-02）：保存到 config.yaml，env 仍可覆盖。
- 校验看的是**即将落盘的内容**（合并后的 config.yaml），不信任前端；
  待修项一次性列全，前端行内提示与后端用同一套判据。
- 已连上的客户端不会热换密钥：返回 ``restart_required``，由界面说明重启后生效。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.config import SecretConfig, load_secrets, secrets_from_data
from app.errors import SetupError

# ---- 校验判据（SRS §5.1 FR-AUTH-01/03/04）----

API_ID_RE = re.compile(r"^\d{5,10}$")
API_HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")
BOT_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
PROXY_SCHEMES = ("socks5", "http")
MAX_PORT = 65535

# 允许写入 config.yaml 的密钥类字段（FR-CFG-01）
SECRET_KEYS = ("api_id", "api_hash", "bot_token", "web_login_secret")


def _load(base_dir: Path) -> dict[str, Any]:
    path = base_dir / "config.yaml"
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def merge_values(data: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """把向导提交的值合并进 config.yaml 内容。

    - 传空串/None 的密钥字段视为「不改动」（表单留空 = 保留原值）。
    - ``proxy`` 键出现即代表本次提交决定代理状态：给字典就是启用，给 ``null`` 就是
      显式清除（关掉「走代理」后保存）。
    - 代理的用户名/密码界面不回显（NFR-02），表单没填时沿用已有值——不能因为看不见就删掉。
    """
    merged = dict(data)
    for key in SECRET_KEYS:
        if key in values and values[key] not in (None, ""):
            merged[key] = values[key]
    if "proxy" in values:
        proxy = values["proxy"]
        existing = merged.get("proxy")
        existing = existing if isinstance(existing, dict) else {}
        merged.pop("proxy", None)
        if isinstance(proxy, dict) and proxy.get("hostname"):
            block: dict[str, Any] = {
                **existing,  # 保留未知键（如历史遗留的 enable_proxy）
                "scheme": str(proxy.get("scheme") or "socks5"),
                "hostname": str(proxy["hostname"]),
                "port": int(proxy.get("port") or 1080),
            }
            for key in ("username", "password"):
                if proxy.get(key):
                    block[key] = proxy[key]
            merged["proxy"] = block
    return merged


def validate_secrets(cfg: SecretConfig) -> list[str]:
    """校验密钥段；返回待修项文案（空列表 = 通过）。"""
    problems: list[str] = []
    if not API_ID_RE.match(str(cfg.api_id)):
        problems.append("api_id 是 5–10 位数字（my.telegram.org → App 页复制）")
    if not API_HASH_RE.match(cfg.api_hash):
        problems.append("api_hash 是 32 位十六进制")
    if cfg.bot_token and not BOT_TOKEN_RE.match(cfg.bot_token):
        problems.append("bot_token 形如 123456:ABC-DEF…（从 @BotFather 复制）")
    proxy = cfg.proxy
    if proxy is not None:
        if not proxy.hostname:
            problems.append("代理要填地址")
        if proxy.scheme not in PROXY_SCHEMES:
            problems.append("代理协议只支持 socks5 / http")
        if not 1 <= proxy.port <= MAX_PORT:
            problems.append(f"代理端口是 1–{MAX_PORT}")
    return problems


def save_secrets(base_dir: Path, values: dict[str, Any]) -> SecretConfig:
    """合并 → 校验 → 写入 config.yaml；校验不过抛 SetupError（文件不动）。

    允许的键：api_id, api_hash, bot_token, web_login_secret,
    proxy: {scheme, hostname, port, username?, password?}（null 表示清除代理）。
    """
    merged = merge_values(_load(base_dir), values)
    problems = validate_secrets(secrets_from_data(merged))
    if problems:
        raise SetupError("；".join(problems) + "。")
    (base_dir / "config.yaml").write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return load_secrets(base_dir)


def setup_complete(secrets: SecretConfig, connected: bool) -> bool:
    """放行判据（FR-OPS-02）：密钥齐备**且**已登录；音乐源可选（向导第 3 步）。

    只配了密钥却登不进去时，控制台里的每一条接口都不可用，故不放行。
    """
    return secrets.has_credentials and connected


# 重新初始化要清掉的键：向导第 1 步的三项 + 代理段（FR-OPS-02）。
# ``web_login_secret`` 是控制台口令，不是向导内容；清掉它会让 0.0.0.0 绑定失去保护。
RESET_KEYS = ("api_id", "api_hash", "bot_token", "proxy")


def clear_secrets(base_dir: Path) -> SecretConfig:
    """重新执行初始化（FR-OPS-02）：删除密钥段，保留 Web 认证、端口与部署路径。

    只删密钥类键，不动 ``web_host``/``web_port``/``web_login_secret`` 与
    ``save_directory`` 等路径键——那些不是向导要重新确认的内容。
    """
    data = _load(base_dir)
    for key in RESET_KEYS:
        data.pop(key, None)
    (base_dir / "config.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return load_secrets(base_dir)
