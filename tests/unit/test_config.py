"""config.py 路径布局（FR-CFG-02）：相对展开、绝对路径、Windows 盘根写法与 env 覆盖。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import app_dirs, load_path_config, resolve_dir


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
