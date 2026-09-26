"""ChKSz 各平台的音质阶梯（SDD §2.7）。

**阶梯是语义档位，不是上游的原生值。** 三个平台管同一档听力的叫法全不一样：
网易把 320k 叫 ``exhigh``、无损叫 ``lossless``、母带叫 ``jymaster``；QQ 与酷狗
直接叫 ``320k``/``flac``/``master``。设置项写「320k」、弹窗选「320k」、前端传
``320k``，到了网易那边才翻成 ``exhigh`` —— 语义在前、原生值在后，加平台时也只
要往映射表里加一行。

另有一档**故意不收**：163 还接受 ``jyeffect``（爵士音效）与 ``sky``（杜比全景声），
那两种是**另一套混音**而不是这首歌更高保真的版本，当「音质」摆出来等于骗人。

上游明说不做别名与降级映射，所以这里存的都是各平台认得的原生值；认不出的档位
按「就低不就高」回退，且实际拿到的档位会由适配器如实回报（见 ``FetchResult.level``）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 界面自上而下的顺序，`ladder()` 原样按这个序返回。
#
# 前五档是**保真度阶梯**，由高到低。末两档（杜比全景声、臻品音效）是网易**另一套混音**
# 而非更高保真的版本——它们和母带不是同一条轴上的高低。列在阶梯之下，不跟母带抢
# 「最高」的位置，免得用户为 Atmos 烧掉额度却以为拿到的是母带。
DISPLAY_TIERS: tuple[str, ...] = (
    "master",
    "hires",
    "lossless",
    "320k",
    "128k",
    "sky",
    "jyeffect",
)

# 保真度阶梯的顶档：`normalize()` 兜底用。**不能**取 DISPLAY_TIERS[0] 的语义去凑，
# 也不取末位——那不是「最高」。
TOP_TIER = "master"

_TIER_LABELS: dict[str, str] = {
    "128k": "标准 128k",
    "320k": "高音质 320k",
    "lossless": "无损",
    "hires": "Hi-Res",
    "master": "母带",
    "sky": "杜比全景声",
    "jyeffect": "臻品音效",
}

# 语义档位 → 各平台原生值。QQ 与酷狗只有 128k/320k/hires/master 叫同名：它们把
# 无损叫 flac；网易则是另一套全名（standard/exhigh/lossless/hires/jymaster/jyeffect/sky），
# 且独有两套混音档。
_FLAC_FAMILY = {**{tier: tier for tier in ("128k", "320k", "hires", "master")}, "lossless": "flac"}
_NATIVE: dict[str, dict[str, str]] = {
    "163": {
        "128k": "standard",
        "320k": "exhigh",
        "lossless": "lossless",
        "hires": "hires",
        "master": "jymaster",
        "jyeffect": "jyeffect",
        "sky": "sky",
    },
    "qq": dict(_FLAC_FAMILY),
    "kugo": dict(_FLAC_FAMILY),
}

# 平台原生档位 → 语义档位（把上游回报的 ``level`` 翻回人能读的档位）。
_FROM_NATIVE: dict[str, dict[str, str]] = {
    provider: {native: tier for tier, native in table.items()}
    for provider, table in _NATIVE.items()
}

@dataclass(slots=True, frozen=True)
class QualityOption:
    """一个可选音质：语义档位 + 界面标签 + 是否为最高档。"""

    tier: str
    label: str
    best: bool


def ladder(provider: str) -> tuple[QualityOption, ...]:
    """某平台的阶梯，按 ``DISPLAY_TIERS`` 的序（界面自上而下即此序）。

    平台没有的档位直接滤掉：网易有七档，QQ 与酷狗只有五档，各自只列自己有的。
    """
    table = _NATIVE.get(provider)
    if not table:
        return ()
    tiers = [tier for tier in DISPLAY_TIERS if tier in table]
    return tuple(
        QualityOption(tier=tier, label=_TIER_LABELS[tier], best=tier == TOP_TIER) for tier in tiers
    )


def normalize(provider: str, tier: str | None, default: str) -> str:
    """把任意来路（设置项、弹窗、批量默认）的档位收敛到该平台阶梯上的一个**语义档位**。

    认不出就退回 ``default``；``default`` 本身也认不出时取该平台最高档——配置写错
    不该让下载直接失败。
    """
    table = _NATIVE.get(provider, {})
    # 也认上游原生名：设置项是用户手写的，早先的版本与外部脚本很可能存了 flac/jymaster
    # 这类值，让它落到同一档上比报错或静默换一档都好。
    for candidate in (tier, default):
        if candidate in table:
            return str(candidate)
        native_match = _FROM_NATIVE.get(provider, {}).get(str(candidate or ""))
        if native_match:
            return native_match
    return TOP_TIER


def native_value(provider: str, tier: str) -> str:
    """语义档位 → 上游原生值（发请求前翻，界面与设置里一律只认语义档位）。"""
    return _NATIVE.get(provider, {}).get(tier, tier)


def tier_of(provider: str, native: str | None) -> str | None:
    """上游回报的原生档位 → 语义档位；认不出返回 None（别硬套一个错的档）。"""
    if not native:
        return None
    return _FROM_NATIVE.get(provider, {}).get(native)


def label_of(tier: str) -> str:
    """语义档位 → 中文标签（下载页展示实际拿到的格式用）。"""
    return _TIER_LABELS.get(tier, tier)
