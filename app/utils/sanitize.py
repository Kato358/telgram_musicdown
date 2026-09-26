"""文件名清洗：非法字符、Windows 保留名、≤230 字节截断（FR-DL-03，SDD §2.2）。

落盘前必经本模块；禁止绕过 sanitize 直接 open() 用户可控路径（编码规范 §2.7）。
"""

from __future__ import annotations

import re

MAX_PATH_BYTES = 230

# Windows 非法字符：< > : " / \ | ? * 与控制字符
ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows 保留名（不分大小写，含带扩展名形式）
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 段尾不可用字符（Windows）：空格与点
TRAILING_STRIP = " ."


def sanitize_segment(name: str) -> str:
    """清洗单段文件名/目录名：非法字符替换 ``_``、保留名检查、段尾空格点剔除。"""
    cleaned = ILLEGAL_CHARS.sub("_", name)
    stem, dot, ext = cleaned.partition(".")
    if stem.upper() in RESERVED_NAMES:
        stem = f"_{stem}"
    cleaned = stem + dot + ext if dot else stem
    return cleaned.rstrip(TRAILING_STRIP) or "_"


def truncate_to_bytes(path_str: str, limit: int = MAX_PATH_BYTES) -> str:
    """整路径 ≤limit 字节截断（UTF-8），从最深段向前逐段截，保留扩展名。"""
    raw = path_str.encode("utf-8")
    if len(raw) <= limit:
        return path_str
    segs = re.split(r"([/\\]+)", path_str)  # 保留分隔符的交替列表
    # 从最深段向前裁字节，保留分隔符结构与扩展名
    total = len(raw)

    def cut_bytes(text: str, budget: int) -> str:
        """从 text 头部保留 ≤budget 字节（不劈多字节字符）。"""
        used = 0
        out: list[str] = []
        for ch in text:
            b = len(ch.encode("utf-8"))
            if used + b > budget:
                break
            out.append(ch)
            used += b
        return "".join(out)

    # 段偶数索引为路径段，奇数为分隔符；从最深段（倒数）向前
    for idx in range(len(segs) - 1, -1, -1):
        if total <= limit:
            break
        seg = segs[idx]
        if not seg or set(seg) <= {"/", "\\"}:
            continue
        seg_bytes = len(seg.encode("utf-8"))
        allowed = seg_bytes - (total - limit)
        if allowed <= 0:
            segs[idx] = ""
            total -= seg_bytes
            continue
        if idx == len(segs) - 1:
            # 尾段：优先保留扩展名（rpartition 取最后一点，含点）
            stem, dot, ext = seg.rpartition(".")
            if dot:
                ext_full = dot + ext
                ext_bytes = len(ext_full.encode("utf-8"))
                segs[idx] = cut_bytes(stem, allowed - ext_bytes) + ext_full
            else:
                segs[idx] = cut_bytes(seg, allowed)
        else:
            segs[idx] = cut_bytes(seg, allowed)
        total = len("".join(segs).encode("utf-8"))
    return re.sub(r"[/\\]+", "/", "".join(segs)).strip("/\\") or "_"
