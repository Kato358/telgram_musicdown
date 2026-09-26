"""依赖倒置的抽象侧（DIP，编码规范 §1.3）。

- ``IStore``：数据访问端口——服务层只依赖它，不依赖具体 SQLite 实现；
  ``Store`` 是它的 SQLite 适配器，测试里的假仓储实现同一协议。
- ``TelegramClientProto``：Telegram 客户端端口——下载所需的最小协议面。
- ``MusicSourceProto``：音乐来源端口——「能搜 + 能取」的统一协议面。Telegram 音乐源
  频道与 ChKSz 在线源是它的两个适配器，搜索/下载/试听只依赖它。

依赖方向：services → ports ← adapters（db / telegram / chksz）。
"""

from app.ports.music import (
    FetchRef,
    FetchResult,
    MusicSourceIndexProto,
    MusicSourceProto,
)
from app.ports.repository import IStore
from app.ports.telegram import TelegramClientProto

__all__ = [
    "FetchRef",
    "FetchResult",
    "IStore",
    "MusicSourceIndexProto",
    "MusicSourceProto",
    "TelegramClientProto",
]
