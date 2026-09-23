<script lang="ts">
  /** 搜索（设计规范 §10）：只在已启用源内搜音频（FR-SEARCH-01）。
   *
   * 结果行是「曲目单」而不是卡片；试听按 FR-PLAY-02 先把消息音频缓存到 temp/preview，
   * 再用返回的 preview_id 流出——所以每行的流地址是各自的 preview_id，不是 message_id。
   * 顶栏搜索通过 `?q=` 进入本页，入参只在变化时消费一次。
   */
  import { onMount } from "svelte";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { api, errorText } from "$lib/api/client";
  import type { SearchResponse, SearchResult, SourceRow } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf, router } from "$lib/router.svelte";
  import { player, type Track } from "$lib/stores/player.svelte";
  import { Button } from "$lib/components/ui/button";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import TrackRow, { trackColumns, type RowMenuItem } from "$lib/components/app/TrackRow.svelte";

  interface UnreachableSource {
    source_id: number;
    reason: string;
  }

  let query = $state("");
  let sources = $state<SourceRow[]>([]);
  /** 选中源 id；空数组 = 全部启用源（与后端 source_ids 语义一致）。 */
  let selected = $state<number[]>([]);
  let results = $state<SearchResult[]>([]);
  let meta = $state<Record<string, unknown>>({});
  let searched = $state(false);
  let searching = $state(false);
  let error = $state("");
  /** `${chat_id}-${message_id}` → preview_id（缓存的试听文件）。 */
  let previews = $state<Record<string, number>>({});
  let pending = $state<Record<string, true>>({});
  let rowError = $state<Record<string, string>>({});
  /** 已加入队列的行：给的是成功提示，不是错误。 */
  let queued = $state<Record<string, true>>({});
  /** 已经消费过的 `?q=` 入参，避免同一个关键词反复触发搜索。 */
  let handledQuery = "";

  const columns = $derived(trackColumns());
  const unreachable = $derived((meta.unreachable as UnreachableSource[] | undefined) ?? []);
  const needSources = $derived(meta.reason === "no_enabled_sources");
  const enabledSources = $derived(sources.filter((source) => source.enabled));
  const resultsCount = $derived(results.length);

  $effect(() => {
    const keyword = router.query.get("q")?.trim() ?? "";
    if (keyword.length === 0 || keyword === handledQuery) return;
    handledQuery = keyword;
    query = keyword;
    void runSearch();
  });

  function setRowError(key: string, message: string) {
    rowError = { ...rowError, [key]: message };
  }

  function clearRowError(key: string) {
    const next = { ...rowError };
    delete next[key];
    rowError = next;
  }

  async function runSearch() {
    const keyword = query.trim();
    if (!keyword) return;
    searching = true;
    error = "";
    try {
      const resp = await api.post<SearchResponse>("/api/search", {
        q: keyword,
        source_ids: selected.length > 0 ? selected : undefined,
        page: 0,
      });
      results = resp.results;
      meta = resp.meta;
      previews = {};
      pending = {};
      rowError = {};
      queued = {};
      searched = true;
    } catch (err) {
      error = errorText(err, t("common.error"));
    } finally {
      searching = false;
    }
  }

  function toggleSource(id: number) {
    selected = selected.includes(id) ? selected.filter((value) => value !== id) : [...selected, id];
  }

  function playFrom(activeKey: string) {
    const playable = results.filter(
      (item) => previews[`${item.chat_id}-${item.message_id}`] !== undefined,
    );
    const tracks: Track[] = playable.map((item) => ({
      id: `${item.chat_id}-${item.message_id}`,
      title: item.title ?? t("common.unknown"),
      artist: item.artist,
      streamUrl: `/api/preview/${previews[`${item.chat_id}-${item.message_id}`]}/stream`,
    }));
    const index = tracks.findIndex((track) => track.id === activeKey);
    if (index >= 0) player.play(tracks, index);
  }

  async function preview(item: SearchResult) {
    const key = `${item.chat_id}-${item.message_id}`;
    clearRowError(key);
    if (previews[key] === undefined) {
      pending = { ...pending, [key]: true };
      try {
        const resp = await api.post<{ preview_id: number }>("/api/preview", {
          message_refs: [
            { chat_id: item.chat_id, message_id: item.message_id, file_size: item.file_size },
          ],
        });
        previews = { ...previews, [key]: resp.preview_id };
      } catch (err) {
        setRowError(key, errorText(err, t("common.error")));
        return;
      } finally {
        const next = { ...pending };
        delete next[key];
        pending = next;
      }
    }
    playFrom(key);
  }

  async function download(item: SearchResult) {
    const key = `${item.chat_id}-${item.message_id}`;
    clearRowError(key);
    try {
      await api.post("/api/downloads", {
        message_refs: [{ chat_id: item.chat_id, message_id: item.message_id }],
      });
      queued = { ...queued, [key]: true };
    } catch (err) {
      setRowError(key, errorText(err, t("common.error")));
    }
  }

  function menuFor(item: SearchResult): RowMenuItem[] {
    return [{ label: t("search.download"), onselect: () => void download(item) }];
  }

  function keyOf(item: SearchResult): string {
    return `${item.chat_id}-${item.message_id}`;
  }

  function playLabelOf(key: string): string {
    if (pending[key]) return t("search.buffering");
    return t("search.preview");
  }

  onMount(async () => {
    try {
      sources = await api.get<SourceRow[]>("/api/sources");
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  });
</script>

<PageHeader title={t("search.title")} lede={t("search.lede")}>
  {#snippet aside()}
    {#if searched}
      <span class="tabular text-caption text-muted-foreground">
        {t("search.resultsCount", { n: resultsCount })}
      </span>
    {/if}
  {/snippet}
</PageHeader>

{#if enabledSources.length === 0}
  <EmptyState title={t("search.needSources")} hint={t("dashboard.needSourcesHint")}>
    {#snippet actions()}
      <Button size="lg" onclick={() => navigate(pathOf("sources"))}>
        {t("search.needSourcesAction")}
      </Button>
    {/snippet}
  </EmptyState>
{/if}

<div class="card flex flex-col gap-4 p-4 md:p-5">
  <form
    class="flex flex-wrap items-center gap-3"
    onsubmit={(event) => {
      event.preventDefault();
      void runSearch();
    }}
  >
    <div
      class="flex h-10 min-w-52 flex-1 items-center gap-2 rounded-full border border-border bg-surface-subtle px-3.5"
    >
      <SearchIcon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <input
        bind:value={query}
        type="search"
        class="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-muted-foreground"
        placeholder={t("search.placeholder")}
        aria-label={t("search.title")}
      />
    </div>
    <Button class="h-10 px-4" type="submit" disabled={searching || query.trim().length === 0}>
      {searching ? t("search.running") : t("search.run")}
    </Button>
  </form>

  {#if enabledSources.length > 0}
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-caption text-muted-foreground">{t("search.filterSources")}</span>
      <button
        type="button"
        class="ui-transition rounded-full px-3 py-1 text-caption {selected.length === 0
          ? 'bg-primary-soft text-primary'
          : 'border border-border text-muted-foreground hover:bg-rule hover:text-foreground'}"
        onclick={() => (selected = [])}
      >
        {t("search.allSources")}
      </button>
      {#each enabledSources as source (source.id)}
        <button
          type="button"
          class="ui-transition rounded-full px-3 py-1 text-caption {selected.includes(source.id)
            ? 'bg-primary-soft text-primary'
            : 'border border-border text-muted-foreground hover:bg-rule hover:text-foreground'}"
          onclick={() => toggleSource(source.id)}
        >
          {source.title}
        </button>
      {/each}
    </div>
  {/if}
</div>

{#if error}
  <Note tone="fail">{error}</Note>
{/if}

{#each unreachable as item (item.source_id)}
  <Note tone="fail">
    <span class="font-medium">
      {sources.find((source) => source.id === item.source_id)?.title ?? `#${item.source_id}`}
    </span>
    <span class="block">
      {item.reason === "flood_wait" ? t("search.floodwait") : t("sources.unreachable")}
    </span>
  </Note>
{/each}

{#if needSources}
  <Note tone="wait">{t("search.needSources")}</Note>
{:else if searched && results.length === 0}
  <EmptyState title={t("search.resultsNone")} />
{:else if !searched}
  <p class="max-w-[40ch] text-body text-muted-foreground">{t("search.idleHint")}</p>
{:else}
  <DataTable {columns}>
    {#each results as item, index (keyOf(item))}
      {@const key = keyOf(item)}
      {@const playing = player.current?.id === key}
      <TrackRow
        {columns}
        {index}
        title={item.title ?? t("common.unknown")}
        artist={item.artist}
        subtitle={item.channel_title}
        duration={item.duration_sec}
        size={item.file_size}
        playing={playing}
        playLabel={playLabelOf(key)}
        onplay={() => void preview(item)}
        menu={menuFor(item)}
      >
        {#snippet feedback()}
          {#if queued[key]}
            <Note tone="done">{t("search.queued")}</Note>
          {:else if rowError[key]}
            <Note tone="fail">{rowError[key]}</Note>
          {/if}
        {/snippet}
      </TrackRow>
    {/each}
  </DataTable>
{/if}
