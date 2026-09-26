"""本地曲库扫描（FR-LIB）：把 save_path 下的音频文件索引进 local_tracks 台账。

- 启动后台扫一次 + 曲库页手动重扫；下载完成的新文件由前端借 SSE 事件触发增量重扫。
- 变化检测只看 (mtime, file_size)：没变的文件不重读标签，几千首的重扫是纯 stat 级。
- 文件消失不改记录、只标 missing=1——「删除后曲库还能看到」是这条台账的存在理由；
  标签缺值回退文件名 / 父目录名（按本项目的落盘模板，父目录通常就是歌手）。
- mutagen 同步 IO，全在 to_thread 里跑（编码规范 §2.3）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from app.db.models import LocalTrack
from app.domain import LIBRARY_AUDIO_EXTS
from app.events import Event, EventBus
from app.ports import IStore
from app.services.tags import TagService

logger = logging.getLogger(__name__)

AUDIO_EXTS = LIBRARY_AUDIO_EXTS


class LocalLibraryService:
    """曲库扫描：磁盘清单 ↔ local_tracks 台账对账。"""

    def __init__(
        self,
        store: IStore,
        save_path: Path,
        tags: TagService,
        events: EventBus | None = None,
    ) -> None:
        self.store = store
        self.save_path = save_path
        self.tags = tags
        self.events = events
        self._scanning = asyncio.Lock()

    async def rescan(self) -> dict[str, int]:
        """重扫一次（并发触发收敛成一次在跑的扫描）。"""
        if self._scanning.locked():
            return {"skipped": 1}
        async with self._scanning:
            result = await asyncio.to_thread(self.scan_sync)
        logger.info(
            "library scan: added=%(added)d updated=%(updated)d missing=%(missing)d total=%(total)d",
            result,
        )
        if self.events is not None:
            await self.events.publish(Event("library.scan", {**result, "done": True}))
        return result

    def scan_sync(self) -> dict[str, int]:
        """同步扫描主体：对账磁盘清单与台账，返回 {added, updated, missing, total}。"""
        added = updated = missing = 0
        if not self.save_path.is_dir():
            return {"added": 0, "updated": 0, "missing": 0, "total": 0}
        db_rows = self.store.local_track_paths()
        history_ids = {
            os.path.normcase(path): track_id
            for path, track_id in self.store.history_paths_by_id().items()
        }
        seen: set[str] = set()
        revived: list[int] = []
        for absolute, stat in self._walk_audio():
            rel = absolute.relative_to(self.save_path).as_posix()
            seen.add(rel)
            row = db_rows.get(rel)
            if row is not None and row.mtime == stat.st_mtime and row.file_size == stat.st_size:
                if row.missing == 1 and row.id is not None:
                    revived.append(row.id)  # 删过的文件又回来了
                continue  # 没变：台账照旧，标签不重读
            track = self._read_track(absolute, rel, stat, row, history_ids)
            self.store.upsert_local_track(track)
            if row is None:
                added += 1
            else:
                updated += 1
        # 台账里有、磁盘上没有的 → 标 missing（记录保留，曲库仍可见）
        gone_ids = [
            row.id
            for rel, row in db_rows.items()
            if rel not in seen and row.missing == 0 and row.id is not None
        ]
        self.store.set_local_track_missing(gone_ids, missing=True)
        self.store.set_local_track_missing(revived, missing=False)
        missing = len(gone_ids)
        return {
            "added": added,
            "updated": updated,
            "missing": missing,
            "total": len(seen),
        }

    def _walk_audio(self) -> list[tuple[Path, os.stat_result]]:
        """收集曲库根下的音频文件（含 stat），跳过访问不了的部分。"""
        found: list[tuple[Path, os.stat_result]] = []
        for root, dirs, files in os.walk(self.save_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if Path(name).suffix.lower() not in AUDIO_EXTS:
                    continue
                path = Path(root) / name
                try:
                    found.append((path, path.stat()))
                except OSError:
                    continue
        return found

    def _read_track(
        self,
        absolute: Path,
        rel: str,
        stat: os.stat_result,
        row: LocalTrack | None,
        history_ids: dict[str, int],
    ) -> LocalTrack:
        """读一个文件的标签与流信息 → 台账行（标签缺值回退文件名/目录名）。"""
        facts = self.tags.media_facts(absolute)
        title = artist = album = None
        try:
            tags = self.tags.read_tags(absolute)
            title, artist, album = tags.get("title"), tags.get("artist"), tags.get("album")
        except Exception:  # noqa: BLE001  坏标签/只读容器按无标签入库（title 回退文件名）
            logger.debug("no tags for %s", absolute)
        stem = absolute.stem
        if not title:
            title = stem
        if not artist:
            # 落盘模板默认按歌手建目录：根直下的文件不硬编目录名
            parent = absolute.parent.name
            artist = parent if absolute.parent != self.save_path else None
        norm = os.path.normcase(str(absolute))
        return LocalTrack(
            id=row.id if row else None,
            rel_path=rel,
            file_name=absolute.name,
            ext=absolute.suffix.lower().lstrip(".") or None,
            title=title,
            artist=artist,
            album=album,
            duration_sec=facts["duration_sec"],
            file_size=facts["file_size"],
            bitrate=facts["bitrate"],
            mtime=stat.st_mtime,
            missing=0,
            history_id=history_ids.get(norm, row.history_id if row else None),
            first_seen_at=row.first_seen_at if row else "",
        )
