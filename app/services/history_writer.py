"""下载历史的落库协作（SRP）：把「任务完成/失败后如何结算历史行」从队列引擎拆出。

下载页的每一列都以 history 行为事实源，而写这些列需要一并处理：
- 展示字段（标题/歌手/专辑）与读数（时长/大小/码率）的回填顺序；
- 落盘文件的内嵌标签优先级高于入队时的元数据（FR-META-01）；
- TG 原文件名 → 「歌手 - 歌名」的 caption 正则兜底；
- 标签写入失败不影响任务终态（NFR-10）。

原先这些方法散在 DownloadService 里（占该 God class 约 10 个方法），
与队列/状态机职责无关；独立成类后既能单测，也让下载服务只关心调度。
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.db.models import History
from app.domain import TemplateConfig, TrackMeta
from app.ports import IStore
from app.services.tags import TagService

logger = logging.getLogger(__name__)


def history_row(meta: TrackMeta, source_id: int | None) -> History:
    """入队即建 history 行（status=queued）：历史页与恢复流程都以它为准。"""
    return History(
        id=None,
        chat_id=meta.chat_id,
        message_id=meta.message_id,
        source_id=source_id,
        file_unique_id=meta.unique_id,
        title=meta.title,
        artist=meta.artist,
        album=meta.album,
        duration_sec=meta.duration_sec,
        file_size=meta.file_size,
        bitrate=meta.bitrate,
        mime=meta.mime,
        ext=meta.ext,
        caption=meta.caption,
        message_date=meta.message_date,
        status="queued",
    )


class HistoryWriter:
    """下载历史行的展示字段 / 读数 / 终态结算。"""

    def __init__(self, store: IStore, tags: TagService, cfg: TemplateConfig) -> None:
        self._store = store
        self._tags = tags
        self._cfg = cfg

    def apply_template(self, cfg: TemplateConfig) -> None:
        """模板热更新（标题兜底正则来自它）。"""
        self._cfg = cfg

    def sync_display(self, history_id: int, meta: TrackMeta) -> None:
        """把 meta 里的展示与读数字段同步进历史行（下载页各列的事实源）。

        展示字段（标题/歌手/专辑）只在 meta 给得出时写；时长/大小/码率在链接入队的
        场景要等 hydrate 或落盘实测才有值，有真值就回填（store 层只认非空更新）。
        """
        if meta.title or meta.artist or meta.album:
            history = self._store.get_history(history_id)
            if history is not None:
                self._store.set_history_display_tags(
                    history_id,
                    meta.title or history.title,
                    meta.artist or history.artist,
                    meta.album or history.album,
                )
        if meta.duration_sec or meta.file_size or meta.bitrate or meta.ext:
            self._store.set_history_media(
                history_id, meta.duration_sec, meta.file_size, meta.bitrate, meta.ext
            )

    def display_meta(self, save_path: str, meta: TrackMeta) -> TrackMeta:
        """完成态的显示元数据（FR-META-01）：落盘文件的内嵌标签是事实源，缺项再补。

        补全顺序：文件标签 → Telegram 原文件名拆「歌手 - 歌名」（caption 正则同一份配置）
        → 入队时的元数据。下载中的行不动（显示 TG 原文件名），完成后才按文件补全。
        """
        title, artist, album = meta.title, meta.artist, meta.album
        try:
            tags = self._tags.read_tags(Path(save_path))
        except Exception:  # noqa: BLE001  只读容器/损坏文件按「无标签」处理，不挡完成流程
            tags = {}
        else:
            title = tags.get("title") or title
            artist = tags.get("artist") or artist
            album = tags.get("album") or album
        # 标题还是 TG 原文件名兜底（带扩展名）：去掉扩展名，并按「歌手 - 歌名」补齐缺失项
        if title and meta.ext and title.lower().endswith(f".{meta.ext.lower()}"):
            title = title[: -len(meta.ext) - 1]
            if not artist:
                m = re.match(self._cfg.caption_artist_re, title)
                if m:
                    artist = m.group("artist").strip() or None
                    title = m.group("title").strip() or title
        return replace(meta, title=title, artist=artist, album=album)

    def finish(
        self, history_id: int, save_path: str, payload: dict[str, Any], meta: TrackMeta
    ) -> None:
        """落盘完成：结算历史行 + 按落盘实测回填读数 + 写标签（失败不影响终态）。"""
        self._store.mark_history_status(history_id, "success", save_path=save_path, finished=True)
        # 时长/大小/码率以落盘文件实测为准（链接入队时这些读数都是空的）
        path = Path(save_path)
        facts = self._tags.media_facts(path)
        measured = replace(
            meta,
            duration_sec=facts["duration_sec"] or meta.duration_sec,
            file_size=facts["file_size"] or meta.file_size,
            bitrate=facts["bitrate"] or meta.bitrate,
        )
        self.sync_display(history_id, self.display_meta(save_path, measured))
        if payload.get("write_tags", True):
            try:
                self._tags.write_tags(path, meta)
            except Exception:
                logger.exception("tag write failed for %s (task continues)", save_path)

    def backfill_media(self, limit: int = 500) -> int:
        """启动回填：旧版本入队的已落盘记录缺时长/大小/码率，按文件补一次。

        文件已被手动移走的行不编造，保持原样留给用户重下。
        """
        count = 0
        for row in self._store.list_history_missing_media(limit=limit):
            if row.id is None or not row.save_path:
                continue
            path = Path(row.save_path)
            if not path.exists():
                continue
            facts = self._tags.media_facts(path)
            if facts["duration_sec"] or facts["file_size"] or facts["bitrate"]:
                self._store.set_history_media(
                    int(row.id), facts["duration_sec"], facts["file_size"], facts["bitrate"]
                )
                count += 1
        return count
