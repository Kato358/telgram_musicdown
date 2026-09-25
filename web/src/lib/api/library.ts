/** 曲库 → 播放队列的桥：把「已入库」记录拉全并映射成播放器的 Track。
 *
 * 行内播放键的队列上下文是整个曲库（而不是屏上可见的那几行）：
 * 后端 /api/history 每页 50 条，这里按筛选条件分页拉到取尽为止。
 */

import { api } from "./client";
import type { HistoryRow } from "./types";
import type { Track } from "$lib/stores/player.svelte";

/** 与后端 `list_history(limit=50)` 对齐。 */
const PAGE_SIZE = 50;

/** 防御上限：20 页（1000 首）。异常大的库不再往上翻，队列够用为先。 */
const MAX_PAGES = 20;

export function rowToTrack(row: HistoryRow): Track {
  return {
    id: String(row.id),
    title: row.title ?? "",
    artist: row.artist,
    streamUrl: `/api/history/${row.id}/stream`,
  };
}

/** 按筛选条件拉全可播曲目（save_path 非空才算在库）。 */
export async function fetchTracks(filter: { q?: string; status?: string } = {}): Promise<Track[]> {
  const tracks: Track[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const parts: string[] = [];
    if (filter.q) parts.push(`q=${encodeURIComponent(filter.q)}`);
    if (filter.status) parts.push(`status=${encodeURIComponent(filter.status)}`);
    parts.push(`page=${page}`);
    const rows = await api.get<HistoryRow[]>(`/api/history?${parts.join("&")}`);
    for (const row of rows) {
      if (row.save_path !== null) tracks.push(rowToTrack(row));
    }
    if (rows.length < PAGE_SIZE) break;
  }
  return tracks;
}
