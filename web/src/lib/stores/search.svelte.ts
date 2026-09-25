/** 搜索状态（模块级持久化）：切页不丢结果。
 *
 * 之前 SearchView 的状态在组件内，App 用 {#key router.key} 切页即销毁，
 * 导致每次回到搜索页都要重搜。现在把状态放到模块级 runes store，
 * 组件只是它的视图层；只有「新一轮搜索」才清空结果。
 */

import { api, errorText } from "$lib/api/client";
import type { SearchResponse, SearchResult, SourceRow } from "$lib/api/types";
import { t } from "$lib/i18n/index.svelte";
import { fly, type FlyOrigin } from "$lib/stores/fly.svelte";
import { player, type Track } from "$lib/stores/player.svelte";

interface UnreachableSource {
  source_id: number;
  reason: string;
}

/** 飞片用的封面：消息带内嵌缩略图才给 URL（没有就飞音符占位），与行内封面同源。 */
function coverOf(item: SearchResult): string | null {
  return item.has_thumb
    ? `/api/search/cover?chat_id=${item.chat_id}&message_id=${item.message_id}`
    : null;
}

class SearchStore {
  query = $state("");
  sources = $state<SourceRow[]>([]);
  /** 选中源 id；空数组 = 全部启用源（与后端 source_ids 语义一致）。 */
  selected = $state<number[]>([]);
  results = $state<SearchResult[]>([]);
  meta = $state<Record<string, unknown>>({});
  searched = $state(false);
  searching = $state(false);
  error = $state("");
  /** `${chat_id}-${message_id}` → preview_id（缓存的试听文件）。 */
  previews = $state<Record<string, number>>({});
  pending = $state<Record<string, true>>({});
  rowError = $state<Record<string, string>>({});
  /** 多选批量下载：selectMode 开启后行首出现勾选框，selection 是勾中的行 key 集合。 */
  selectMode = $state(false);
  selection = $state<Set<string>>(new Set());
  /** sources 只需拉一次；组件挂载时按需补拉。 */
  sourcesLoaded = $state(false);

  get unreachable(): UnreachableSource[] {
    return (this.meta.unreachable as UnreachableSource[] | undefined) ?? [];
  }

  get needSources(): boolean {
    return this.meta.reason === "no_enabled_sources";
  }

  get enabledSources(): SourceRow[] {
    return this.sources.filter((source) => source.enabled);
  }

  keyOf(item: SearchResult): string {
    return `${item.chat_id}-${item.message_id}`;
  }

  async loadSources() {
    if (this.sourcesLoaded) return;
    this.sourcesLoaded = true;
    try {
      this.sources = await api.get<SourceRow[]>("/api/sources");
    } catch (err) {
      this.sourcesLoaded = false;
      this.error = errorText(err, t("common.error"));
    }
  }

  async runSearch() {
    const keyword = this.query.trim();
    if (!keyword) return;
    this.searching = true;
    this.error = "";
    try {
      const resp = await api.post<SearchResponse>("/api/search", {
        q: keyword,
        source_ids: this.selected.length > 0 ? this.selected : undefined,
        page: 0,
      });
      this.results = resp.results;
      this.meta = resp.meta;
      this.previews = {};
      this.pending = {};
      this.rowError = {};
      this.exitSelect();
      this.searched = true;
    } catch (err) {
      this.error = errorText(err, t("common.error"));
    } finally {
      this.searching = false;
    }
  }

  toggleSource(id: number) {
    this.selected = this.selected.includes(id)
      ? this.selected.filter((value) => value !== id)
      : [...this.selected, id];
  }

  toggleSelectMode() {
    this.selectMode = !this.selectMode;
    this.selection = new Set();
  }

  /** 退出多选（新一轮搜索也走这里：结果换了，勾选自然作废）。 */
  exitSelect() {
    this.selectMode = false;
    this.selection = new Set();
  }

  /** 换一个新的 Set 而不是就地改：`$state` 的 Set 只有整体替换才触发更新。 */
  toggleSelect(key: string, checked: boolean) {
    this.selection = checked
      ? new Set([...this.selection, key])
      : new Set([...this.selection].filter((value) => value !== key));
  }

  get allSelected(): boolean {
    return this.results.length > 0 && this.results.every((item) => this.selection.has(this.keyOf(item)));
  }

  get someSelected(): boolean {
    return this.selection.size > 0 && !this.allSelected;
  }

  toggleSelectAll(checked: boolean) {
    this.selection = checked ? new Set(this.results.map((item) => this.keyOf(item))) : new Set();
  }

  get selectedResults(): SearchResult[] {
    return this.results.filter((item) => this.selection.has(this.keyOf(item)));
  }

  private playFrom(activeKey: string) {
    const playable = this.results.filter(
      (item) => this.previews[`${item.chat_id}-${item.message_id}`] !== undefined,
    );
    const tracks: Track[] = playable.map((item) => ({
      id: `${item.chat_id}-${item.message_id}`,
      title: item.title ?? t("common.unknown"),
      artist: item.artist,
      streamUrl: `/api/preview/${this.previews[`${item.chat_id}-${item.message_id}`]}/stream`,
    }));
    const index = tracks.findIndex((track) => track.id === activeKey);
    if (index >= 0) player.play(tracks, index);
  }

  async preview(item: SearchResult) {
    const key = `${item.chat_id}-${item.message_id}`;
    this.clearRowError(key);
    if (this.previews[key] === undefined) {
      this.pending = { ...this.pending, [key]: true };
      try {
        const resp = await api.post<{ preview_id: number }>("/api/preview", {
          message_refs: [
            { chat_id: item.chat_id, message_id: item.message_id, file_size: item.file_size },
          ],
        });
        this.previews = { ...this.previews, [key]: resp.preview_id };
      } catch (err) {
        this.setRowError(key, errorText(err, t("common.error")));
        return;
      } finally {
        const next = { ...this.pending };
        delete next[key];
        this.pending = next;
      }
    }
    this.playFrom(key);
  }

  /** 加入下载队列：成功的信号是「飞进侧边栏」（FlyOverlay），行内不再挂提示。 */
  async download(item: SearchResult, origin: FlyOrigin) {
    const key = this.keyOf(item);
    this.clearRowError(key);
    try {
      await api.post("/api/downloads", {
        message_refs: [{ chat_id: item.chat_id, message_id: item.message_id }],
      });
      fly.launch(origin, [coverOf(item)]);
    } catch (err) {
      this.setRowError(key, errorText(err, t("common.error")));
    }
  }

  /** 批量下载：一次请求带全部勾选行，成功后整批飞向侧边栏并退出多选。 */
  async downloadSelected(origin: FlyOrigin) {
    const targets = this.selectedResults;
    if (targets.length === 0) return;
    try {
      await api.post("/api/downloads", {
        message_refs: targets.map((item) => ({
          chat_id: item.chat_id,
          message_id: item.message_id,
        })),
      });
      fly.launch(origin, targets.map(coverOf));
      this.exitSelect();
    } catch (err) {
      this.error = errorText(err, t("common.error"));
    }
  }

  private setRowError(key: string, message: string) {
    this.rowError = { ...this.rowError, [key]: message };
  }

  private clearRowError(key: string) {
    const next = { ...this.rowError };
    delete next[key];
    this.rowError = next;
  }

  playLabelOf(key: string): string {
    if (this.pending[key]) return t("search.buffering");
    return t("search.preview");
  }
}

export const search = new SearchStore();
