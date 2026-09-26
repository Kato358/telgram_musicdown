/** 曲库 → 播放队列的桥：把「已入库」记录拉全并映射成播放器的 Track。
 *
 * 行内播放键的队列上下文是整个曲库（而不是屏上可见的那几行）：
 * 后端 /api/history 每页 50 条，这里按筛选条件分页拉到取尽为止。
 */

import { api } from "./client";
import { coverUrl } from "$lib/cover";
import type { HistoryRow, LocalLibraryResponse, LocalTrackRow } from "./types";
import type { Track } from "$lib/stores/player.svelte";

/** 与后端 `list_history(limit=50)` 对齐。 */
const PAGE_SIZE = 50;

/** 防御上限：20 页（1000 首）。异常大的库不再往上翻，队列够用为先。 */
const MAX_PAGES = 20;

/** 本地曲库分页拉取的单页行数（与曲库页懒加载同参）。 */
export const LIBRARY_PAGE_SIZE = 50;

/** 曲库播放键拉全时的防御上限：20 页 × 200 行。 */
const LIBRARY_MAX_PAGES = 20;

export function rowToTrack(row: HistoryRow): Track {
  return {
    id: String(row.id),
    title: row.title ?? "",
    artist: row.artist,
    streamUrl: `/api/history/${row.id}/stream`,
    cover: coverUrl(row.title, row.artist),
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

/** 本地曲库的筛选参数（列表分页与播放拉全共用一套）。 */
export interface LibraryFilter {
  q?: string;
  /** "1" 已删除 / "0" 在库 / 空缺 = 全部。 */
  missing?: string;
  sort?: string;
  order?: string;
}

function localQuery(filter: LibraryFilter, limit: number, offset: number): string {
  const parts: string[] = [];
  if (filter.q) parts.push(`q=${encodeURIComponent(filter.q)}`);
  if (filter.missing) parts.push(`missing=${encodeURIComponent(filter.missing)}`);
  if (filter.sort) parts.push(`sort=${encodeURIComponent(filter.sort)}`);
  if (filter.order) parts.push(`order=${encodeURIComponent(filter.order)}`);
  parts.push(`limit=${limit}`, `offset=${offset}`);
  return parts.join("&");
}

/** 曲库一页（懒加载每次只拉这一页）。 */
export function fetchLocalPage(
  filter: LibraryFilter,
  offset: number,
  limit: number = LIBRARY_PAGE_SIZE,
): Promise<LocalLibraryResponse> {
  return api.get<LocalLibraryResponse>(`/api/local-library?${localQuery(filter, limit, offset)}`);
}

/** 曲库行 → 播放器 Track：流与封面都走曲库行自己的端点（封面 = 内嵌 > api 兜底）。 */
export function localRowToTrack(row: LocalTrackRow): Track {
  return {
    id: `local-${row.id}`,
    title: row.title ?? row.file_name,
    artist: row.artist,
    streamUrl: `/api/local-library/${row.id}/stream`,
    cover: `/api/local-library/${row.id}/cover`,
  };
}

/** 按当前筛选拉全曲库曲目（播放键的队列上下文；上限 20 页 × 200 行）。 */
export async function fetchAllLocalTracks(filter: LibraryFilter): Promise<Track[]> {
  const tracks: Track[] = [];
  for (let page = 0; page < LIBRARY_MAX_PAGES; page += 1) {
    const data = await fetchLocalPage(filter, page * 200, 200);
    for (const row of data.items) {
      if (!row.missing) tracks.push(localRowToTrack(row));
    }
    if ((page + 1) * 200 >= data.total) break;
  }
  return tracks;
}
