"""config.py 路径布局（FR-CFG-02）与密钥装配：相对展开、绝对路径、Windows 盘根写法与 env 覆盖。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config as app_config
from app.config import app_dirs, load_path_config, load_secrets, resolve_dir, web_dist_dir


def _write_config(base: Path, text: str) -> None:
    (base / "config.yaml").write_text(text, encoding="utf-8")


def test_defaults_are_relative_to_base_dir(tmp_path: Path) -> None:
    dirs = app_dirs(tmp_path)
    assert dirs["save_path"] == tmp_path / "downloads"
    assert dirs["sessions"] == tmp_path / "sessions"
    assert dirs["temp"] == tmp_path / "temp"
    assert dirs["preview"] == tmp_path / "temp" / "preview"
    assert all(d.is_dir() for d in dirs.values())


def test_config_file_relative_values_create_under_base(tmp_path: Path) -> None:
    _write_config(tmp_path, "save_directory: downloads\nsession_directory: tg\n")
    dirs = app_dirs(tmp_path)
    assert dirs["save_path"] == tmp_path / "downloads"
    assert dirs["sessions"] == tmp_path / "tg"
    assert dirs["temp"] == tmp_path / "temp"  # 未配置项保持默认
    assert (tmp_path / "downloads").is_dir()


def test_config_file_absolute_values(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "save_directory: /data/downloads\n"
        "session_directory: /data/sessions\n"
        "temp_directory: /data/temp\n",
    )
    cfg = load_path_config(tmp_path)
    resolved = resolve_dir(cfg.save_directory, tmp_path)
    # 绝对路径原样使用；Windows 上 "/data/..." 锚定到 base_dir 所在盘
    assert resolved.is_absolute()
    assert resolved.parts[-2:] == ("data", "downloads")
    assert resolve_dir(cfg.temp_directory, tmp_path) / "preview" == resolve_dir(
        "/data/temp/preview", tmp_path
    )


def test_env_overrides_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, "save_directory: from-file\n")
    monkeypatch.setenv("TGM_SAVE_DIRECTORY", "from-env")
    assert load_path_config(tmp_path).save_directory == "from-env"


def test_web_login_enabled_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录开关（FR-WEB-02）：yaml/env 双入口；解析不出按开启处理，别把保护静默关掉。"""
    monkeypatch.delenv("TGM_WEB_LOGIN_ENABLED", raising=False)
    # yaml 布尔字面量与带引号字符串写法都要认
    _write_config(tmp_path, "web_login_enabled: false\n")
    assert load_secrets(tmp_path).web_login_enabled is False
    _write_config(tmp_path, 'web_login_enabled: "0"\n')
    assert load_secrets(tmp_path).web_login_enabled is False
    # 未配置 = 默认开启；拼写错误（如 flase）也按开启处理（安全向）
    _write_config(tmp_path, "api_hash: x\n")
    assert load_secrets(tmp_path).web_login_enabled is True
    _write_config(tmp_path, "web_login_enabled: flase\n")
    assert load_secrets(tmp_path).web_login_enabled is True
    # env 覆盖配置文件；留空 = 不覆盖
    _write_config(tmp_path, "web_login_enabled: true\n")
    monkeypatch.setenv("TGM_WEB_LOGIN_ENABLED", "false")
    assert load_secrets(tmp_path).web_login_enabled is False
    monkeypatch.setenv("TGM_WEB_LOGIN_ENABLED", "")
    assert load_secrets(tmp_path).web_login_enabled is True


def test_web_dist_dir_follows_code_not_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 首次部署：Docker 的 TGM_BASE_DIR=/data，产物在代码旁的 web/dist，
    # 拿数据目录拼路径会让首屏 404，初始化向导不可达
    monkeypatch.delenv("TGM_STATIC_DIR", raising=False)
    monkeypatch.setenv("TGM_BASE_DIR", str(tmp_path))
    assert web_dist_dir() == Path(app_config.__file__).resolve().parent.parent / "web" / "dist"
    assert tmp_path not in web_dist_dir().parents


def test_web_dist_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TGM_STATIC_DIR", str(tmp_path / "static"))
    assert web_dist_dir() == (tmp_path / "static").absolute()
