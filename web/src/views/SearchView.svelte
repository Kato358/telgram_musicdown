<script lang="ts">
  /** 搜索：只在已启用源内搜音频（FR-SEARCH-01）。
   *
   * 结果行是「曲目单」而不是卡片；试听按 FR-PLAY-02 先把消息音频缓存到 temp/preview，
   * 再用返回的 preview_id 流出——所以每行的流地址是各自的 preview_id，不是 message_id。
   */
  import { onMount } from "svelte";
  import { api, errorText } from "$lib/api/client";
  import type { SearchResponse, SearchResult, SourceRow } from "$lib/api/types";
  import { formatDuration, formatSize } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { player, type Track } from "$lib/stores/player.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

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
  let queued = $state<Record<string, true>>({});

  const unreachable = $derived((meta.unreachable as UnreachableSource[] | undefined) ?? []);
  const needSources = $derived(meta.reason === "no_enabled_sources");
  const enabledSources = $derived(sources.filter((source) => source.enabled));

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

  onMount(async () => {
    try {
      sources = await api.get<SourceRow[]>("/api/sources");
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  });
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("search.title")} lede={t("search.lede")}>
    {#snippet aside()}
      {#if searched}
        <span class="tabular text-micro text-muted-foreground">
          {t("search.resultsCount", { n: results.length })}
        </span>
      {/if}
    {/snippet}
  </PageHeader>

  {#if enabledSources.length === 0}
    <EmptyState title={t("search.needSources")} hint={t("dashboard.needSourcesHint")}>
      {#snippet actions()}
        <Button onclick={() => navigate(pathOf("sources"))}>{t("search.needSourcesAction")}</Button>
      {/snippet}
    </EmptyState>
  {/if}

  <form
    class="flex flex-wrap items-end gap-2"
    onsubmit={(event) => {
      event.preventDefault();
      void runSearch();
    }}
  >
    <div class="min-w-52 flex-1">
      <Input
        bind:value={query}
        type="search"
        aria-label={t("search.title")}
        placeholder={t("search.placeholder")}
      />
    </div>
    <Button type="submit" disabled={searching || query.trim().length === 0}>
      {searching ? t("search.running") : t("search.run")}
    </Button>
  </form>

  {#if enabledSources.length > 0}
    <div class="flex flex-wrap items-center gap-1.5">
      <span class="mr-1 text-micro text-muted-foreground">{t("search.filterSources")}</span>
      <Button
        variant={selected.length === 0 ? "secondary" : "outline"}
        size="xs"
        onclick={() => (selected = [])}
      >
        {t("search.allSources")}
      </Button>
      {#each enabledSources as source (source.id)}
        <Button
          variant={selected.includes(source.id) ? "secondary" : "outline"}
          size="xs"
          onclick={() => toggleSource(source.id)}
        >
          {source.title}
        </Button>
      {/each}
    </div>
  {/if}

  {#if error}
    <Note tone="fail">{error}</Note>
  {/if}

  {#if unreachable.length > 0}
    <div class="flex flex-col gap-1">
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
    </div>
  {/if}

  {#if needSources}
    <Note tone="wait">{t("search.needSources")}</Note>
  {:else if searched && results.length === 0}
    <EmptyState title={t("search.resultsNone")} />
  {:else if !searched}
    <p class="max-w-[56ch] text-small text-muted-foreground">{t("search.idleHint")}</p>
  {/if}

  <ul class="divide-y divide-rule">
    {#each results as item (item.chat_id + "-" + item.message_id)}
      {@const key = `${item.chat_id}-${item.message_id}`}
      <li class="flex items-center gap-3 py-2.5">
        <div class="min-w-0 flex-1">
          <p class="truncate text-small font-medium">{item.title ?? t("common.unknown")}</p>
          <p class="truncate text-micro text-muted-foreground">
            {[item.artist, item.channel_title].filter(Boolean).join(" | ") || "—"}
          </p>
          {#if rowError[key]}
            <Note tone="fail" class="mt-1.5">{rowError[key]}</Note>
          {/if}
        </div>
        <span class="tabular hidden w-14 shrink-0 text-right text-micro text-muted-foreground sm:block">
          {formatDuration(item.duration_sec)}
        </span>
        <span class="tabular hidden w-20 shrink-0 text-right text-micro text-muted-foreground md:block">
          {formatSize(item.file_size)}
        </span>
        <div class="flex shrink-0 items-center gap-1">
          <Button
            variant={player.current?.id === key ? "secondary" : "outline"}
            size="xs"
            disabled={pending[key]}
            onclick={() => void preview(item)}
          >
            {pending[key] ? t("search.buffering") : t("search.preview")}
          </Button>
          <Button
            variant={queued[key] ? "secondary" : "default"}
            size="xs"
            disabled={queued[key]}
            onclick={() => void download(item)}
          >
            {queued[key] ? t("search.queued") : t("search.download")}
          </Button>
        </div>
      </li>
    {/each}
  </ul>
</div>
