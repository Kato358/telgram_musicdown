"""ChKSz 在线源适配器层（与 ``app/telegram/`` 平级）。

本包只做一件事：把 ChKSz API 的三个平台包装成 ``MusicSourceProto``。
服务层不 import 这里，只依赖 ``app.ports.music`` 的协议。
"""

from app.chksz.client import ChkszClient, ChkszError
from app.chksz.quality import QualityOption, label_of, ladder, normalize
from app.chksz.source import PROVIDER_LABELS, ChkszSource

__all__ = [
    "PROVIDER_LABELS",
    "ChkszClient",
    "ChkszError",
    "ChkszSource",
    "QualityOption",
    "ladder",
    "label_of",
    "normalize",
]
