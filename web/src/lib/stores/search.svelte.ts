/** 搜索状态（模块级持久化）：切页不丢结果。
 *
 * 之前 SearchView 的状态在组件内，App 用 {#key router.key} 切页即销毁，
 * 导致每次回到搜索页都要重搜。现在把状态放到模块级 runes store，
 * 组件只是它的视图层；只有「新一轮搜索」才清空结果。
 */

import { api, errorText } from "$lib/api/client";
import { coverUrl } from "$lib/cover";
import type {
  QualityOption,
  QualityTier,
  QualitiesResponse,
  SearchResponse,
  SearchResult,
  SearchSource,
} from "$lib/api/types";
import { t } from "$lib/i18n/index.svelte";
import { fly, type FlyOrigin } from "$lib/stores/fly.svelte";
import { player, type Track } from "$lib/stores/player.svelte";
import { session } from "$lib/stores/session.svelte";

interface UnreachableSource {
  source_id: number;
  reason: string;
}

/** 排序口径（FR-SEARCH-03）：与后端 `sort` 入参一一对应，列表只此一份。 */
export const SORT_OPTIONS = ["relevance", "date", "duration", "size"] as const;
export type SearchSort = (typeof SORT_OPTIONS)[number];

/** 飞片用的封面：走全局封面链路（与行内封面同源），没有可查字段就飞音符占位。 */
function coverOf(item: SearchResult): string | null {
  return coverUrl(item.title, item.artist);
}

/** 卡片 → 入队/试听的定位载荷。
 *
 *  在线源多带 `provider`（平台）与 `ref`（平台曲目 id）：`message_id` 是哈希，
 *  拿不回平台 id，前端不传就无从解析播放地址。频道行只带两个 id，与旧调用同形。
 */
function refOf(item: SearchResult, quality?: QualityTier, fileSize?: number | null) {
  return {
    chat_id: item.chat_id,
    message_id: item.message_id,
    title: item.title,
    artist: item.artist,
    provider: item.provider,
    ref: item.ref,
    ...(quality ? { quality } : {}),
    ...(fileSize !== undefined && fileSize !== null ? { file_size: fileSize } : {}),
  };
}

class SearchStore {
  query = $state("");
  /** 可搜来源：音乐源频道 + 在线源平台的同一份清单（scope 唯一，id 即勾选值）。 */
  sources = $state<SearchSource[]>([]);
  /** 选中源 id；空数组 = 全部启用源（与后端 source_ids 语义一致）。 */
  selected = $state<number[]>([]);
  /** 排序口径（服务端在合并后的窗口上排序，见 SDD §2.6）。 */
  sort = $state<SearchSort>("relevance");
  results = $state<SearchResult[]>([]);
  meta = $state<Record<string, unknown>>({});
  /** 已载入到第几页（0 起）；「加载更多」逐页追加，分页由服务端在完整结果集上切。 */
  page = $state(0);
  /** 服务端还有更深的页（`meta.has_more`）：决定「加载更多」是否出现。 */
  hasMore = $state(false);
  /** 本次结果不全（`meta.partial`）：有源超了同步窗口，仍在后台补齐。 */
  partial = $state(false);
  /** 仍在后台补齐的源 id（`meta.pending_sources`），提示里点名。 */
  pendingSources = $state<number[]>([]);
  loadingMore = $state(false);
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

  /** 在线源平台（网易云/QQ 音乐/酷狗…）。加新源只需服务端加一项，前端不认平台名。 */
  get onlineSources(): SearchSource[] {
    return this.sources.filter((source) => source.online);
  }

  get channelSources(): SearchSource[] {
    return this.sources.filter((source) => !source.online);
  }

  isOnline(item: SearchResult): boolean {
    return item.provider !== "telegram";
  }

  /** 后台补齐中的源名（提示里点名，缺标题回退 #id）。 */
  get pendingTitles(): string[] {
    return this.pendingSources.map(
      (id) => this.sources.find((source) => source.id === id)?.title ?? `#${id}`,
    );
  }

  keyOf(item: SearchResult): string {
    return `${item.chat_id}-${item.message_id}`;
  }

  async loadSources(force = false) {
    if (this.sourcesLoaded && !force) return;
    this.sourcesLoaded = true;
    try {
      const resp = await api.get<{ sources: SearchSource[] }>("/api/search/sources");
      this.sources = resp.sources;
    } catch (err) {
      this.sourcesLoaded = false;
      this.error = errorText(err, t("common.error"));
    }
  }

  async runSearch() {
    const keyword = this.query.trim();
    if (!keyword) return;
    // 全账号模式绕开音乐源：先入为主的源勾选不能把全局结果缩成某几个对话。
    if (session.globalSearch) this.selected = [];
    this.searching = true;
    this.error = "";
    try {
      const resp = await api.post<SearchResponse>("/api/search", {
        q: keyword,
        source_ids: session.globalSearch
          ? undefined
          : this.selected.length > 0
            ? this.selected
            : undefined,
        sort: this.sort,
        page: 0,
      });
      this.results = resp.results;
      this.applyMeta(resp, 0);
      this.previews = {};
      this.pending = {};
      this.rowError = {};
      this.exitSelect();
      this.searched = true;
    } catch (err) {
      this.error = errorText(err, t("common.error"));
      // 结果没换成新的：别让「加载更多」按旧页码续取
      this.page = 0;
      this.hasMore = false;
      this.partial = false;
      this.pendingSources = [];
    } finally {
      this.searching = false;
    }
  }

  /** 追加下一页（FR-SEARCH-04 跨页）：服务端在完整结果集上切片，续页不重不漏。 */
  async loadMore() {
    if (!this.hasMore || this.loadingMore || this.searching) return;
    const keyword = this.query.trim();
    if (!keyword) return;
    this.loadingMore = true;
    try {
      const next = this.page + 1;
      const resp = await api.post<SearchResponse>("/api/search", {
        q: keyword,
        source_ids: session.globalSearch
          ? undefined
          : this.selected.length > 0
            ? this.selected
            : undefined,
        sort: this.sort,
        page: next,
      });
      // 后台补齐会让同一页重排，按行键去重后再追加
      const seen = new Set(this.results.map((item) => this.keyOf(item)));
      this.results = [
        ...this.results,
        ...resp.results.filter((item) => !seen.has(this.keyOf(item))),
      ];
      this.applyMeta(resp, next);
    } catch (err) {
      this.error = errorText(err, t("common.error"));
    } finally {
      this.loadingMore = false;
    }
  }

  private applyMeta(resp: SearchResponse, page: number) {
    this.meta = resp.meta;
    this.page = page;
    this.hasMore = resp.meta.has_more === true;
    this.partial = resp.meta.partial === true;
    this.pendingSources = Array.isArray(resp.meta.pending_sources)
      ? (resp.meta.pending_sources as number[])
      : [];
  }

  toggleSource(id: number) {
    this.selected = this.selected.includes(id)
      ? this.selected.filter((value) => value !== id)
      : [...this.selected, id];
  }

  /** 换排序口径：已有结果就立即从第 0 页重取。
   *
   * 源多选是「下一轮搜索的条件」，改完要按搜索；排序不同——它只是把**已有结果**
   * 换个排法看，等用户再点一次搜索就是个看起来没反应的控件。窗口已缓存，重取不重打上游。
   */
  setSort(sort: SearchSort) {
    if (sort === this.sort) return;
    this.sort = sort;
    if (this.searched && !this.searching && this.query.trim().length > 0) void this.runSearch();
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
      cover: coverOf(item),
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
          message_refs: [refOf(item, undefined, item.file_size)],
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
  async download(item: SearchResult, origin: FlyOrigin, quality?: QualityTier) {
    const key = this.keyOf(item);
    this.clearRowError(key);
    try {
      await api.post("/api/downloads", { message_refs: [refOf(item, quality)] });
      fly.launch(origin, [coverOf(item)]);
    } catch (err) {
      this.setRowError(key, errorText(err, t("common.error")));
    }
  }

  // ---- 音质弹窗（在线源才有「选哪一档」这回事）----

  /** 正在选音质的行（`null` = 弹窗关着）。 */
  qualityTarget = $state<SearchResult | null>(null);
  /** 弹窗里当前选中的档位。 */
  qualityChoice = $state<QualityTier>("320k");
  /** 各平台阶梯（服务端发的语义档位表，加新源不用改前端）。 */
  ladders = $state<Record<string, QualityOption[]>>({});
  private laddersLoaded = false;

  /** 点在线源行的下载：先问档位，再入队。频道行直接下，没有这一步。 */
  async requestDownload(item: SearchResult, origin: FlyOrigin) {
    if (!this.isOnline(item)) {
      await this.download(item, origin);
      return;
    }
    await this.loadLadders();
    this.qualityChoice = this.defaultTierFor(item.provider);
    this.qualityOrigin = origin;
    this.qualityTarget = item;
  }

  /** 弹窗确认：按所选档位入队。 */
  async confirmQuality() {
    const item = this.qualityTarget;
    if (item === null) return;
    const origin = this.qualityOrigin;
    this.qualityTarget = null;
    await this.download(item, origin, this.qualityChoice);
  }

  /** 该平台的默认档位：设置页的下载默认音质。 */
  defaultTierFor(provider: string): QualityTier {
    const configured = this.ladders[provider]?.find((o) => o.tier === this.defaultDownloadQuality);
    if (configured) return configured.tier;
    return (this.ladders[provider]?.[0]?.tier ?? "320k") as QualityTier;
  }

  private qualityOrigin: FlyOrigin = { x: 0, y: 0 };
  /** 设置页的「下载默认音质」；批量的「下载所选」按它走，不逐行弹窗。 */
  defaultDownloadQuality = $state<QualityTier>("hires");

  async loadLadders(force = false) {
    if (this.laddersLoaded && !force) return;
    const resp = await api.get<QualitiesResponse>("/api/settings/qualities");
    this.ladders = resp.providers;
    this.laddersLoaded = true;
  }

  /** 批量下载：一次请求带全部勾选行，成功后整批飞向侧边栏并退出多选。 */
  async downloadSelected(origin: FlyOrigin) {
    const targets = this.selectedResults;
    if (targets.length === 0) return;
    try {
      await api.post("/api/downloads", {
        // 批量按设置里的默认档走：逐行弹窗会让「全选下载」根本没法用
        message_refs: targets.map((item) => refOf(item, this.defaultDownloadQuality)),
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
