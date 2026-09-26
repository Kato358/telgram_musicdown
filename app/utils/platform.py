"""平台相关的小工具（NFR-07：与 Telegram/web 无关，可单测）。

``open_in_file_manager`` 原先私有在 web/routes/history.py 里，被 library.py 以私有名
跨模块 import（route → route 私有耦合）。它本质是平台适配而非某个路由的私有逻辑，
收在这里让两个路由模块各自依赖公共入口。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_in_file_manager(folder: Path) -> None:
    """用系统文件管理器打开目录（Windows / macOS / Linux 各自的原生方式）。"""
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", str(folder)], check=True)  # noqa: S603
    else:
        subprocess.run(["/usr/bin/xdg-open", str(folder)], check=True)  # noqa: S603
