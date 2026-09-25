<script lang="ts">
  /** 搜索（设计规范 §10）：只在已启用源内搜音频（FR-SEARCH-01）。
   *
   * 状态都在模块级 `search` store（lib/stores/search.svelte.ts），切页不丢：
   * 本组件只是视图层，回到搜索页时上次的关键词和结果列表原样还在。
   * 顶栏搜索通过 `?q=` 进入本页，入参只在变化时消费一次。
   */
  import { onMount } from "svelte";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf, router } from "$lib/router.svelte";
  import { search } from "$lib/stores/search.svelte";
  import { player } from "$lib/stores/player.svelte";
  import { Button } from "$lib/components/ui/button";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import SectionCard from "$lib/components/app/SectionCard.svelte";
  import TrackRow, { trackColumns } from "$lib/components/app/TrackRow.svelte";

  const columns = $derived(trackColumns());
  const resultsCount = $derived(search.results.length);

  $effect(() => {
    const keyword = router.query.get("q")?.trim() ?? "";
    if (keyword.length === 0 || keyword === search.query.trim()) return;
    search.query = keyword;
    void search.runSearch();
  });

  onMount(() => {
    void search.loadSources();
  });
</script>

<PageHeader title={t("search.title")} lede={t("search.lede")}>
  {#snippet aside()}
    {#if search.searched}
      <span class="tabular text-caption text-muted-foreground">
        {t("search.resultsCount", { n: resultsCount })}
      </span>
    {/if}
  {/snippet}
</PageHeader>

{#if search.enabledSources.length === 0}
  <EmptyState title={t("search.needSources")} hint={t("dashboard.needSourcesHint")}>
    {#snippet actions()}
      <Button size="lg" onclick={() => navigate(pathOf("sources"))}>
        {t("search.needSourcesAction")}
      </Button>
    {/snippet}
  </EmptyState>
{/if}

<SectionCard title={t("search.formTitle")} hint={t("search.formHint")} icon={SearchIcon}>
  <div class="flex flex-col gap-4">
    <form
      class="flex flex-wrap items-center gap-3"
      onsubmit={(event) => {
        event.preventDefault();
        void search.runSearch();
      }}
    >
      <div
        class="flex h-10 min-w-52 flex-1 items-center gap-2 rounded-full border border-border bg-surface-subtle px-3.5"
      >
        <SearchIcon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <input
          bind:value={search.query}
          type="search"
          class="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-muted-foreground"
          placeholder={t("search.placeholder")}
          aria-label={t("search.title")}
        />
      </div>
      <Button
        class="h-10 px-4"
        type="submit"
        disabled={search.searching || search.query.trim().length === 0}
      >
        {search.searching ? t("search.running") : t("search.run")}
      </Button>
    </form>

    {#if search.enabledSources.length > 0}
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-caption text-muted-foreground">{t("search.filterSources")}</span>
        <button
          type="button"
          class="ui-transition rounded-full px-3 py-1 text-caption {search.selected.length === 0
            ? 'bg-primary-soft text-primary'
            : 'border border-border text-muted-foreground hover:bg-rule hover:text-foreground'}"
          onclick={() => (search.selected = [])}
        >
          {t("search.allSources")}
        </button>
        {#each search.enabledSources as source (source.id)}
          <button
            type="button"
            class="ui-transition rounded-full px-3 py-1 text-caption {search.selected.includes(
              source.id,
            )
              ? 'bg-primary-soft text-primary'
              : 'border border-border text-muted-foreground hover:bg-rule hover:text-foreground'}"
            onclick={() => search.toggleSource(source.id)}
          >
            {source.title}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</SectionCard>

{#if search.error}
  <Note tone="fail">{search.error}</Note>
{/if}

{#each search.unreachable as item (item.source_id)}
  <Note tone="fail">
    <span class="font-medium">
      {search.sources.find((source) => source.id === item.source_id)?.title ?? `#${item.source_id}`}
    </span>
    <span class="block">
      {item.reason === "flood_wait" ? t("search.floodwait") : t("sources.unreachable")}
    </span>
  </Note>
{/each}

{#if search.needSources}
  <Note tone="wait">{t("search.needSources")}</Note>
{:else if search.searched && search.results.length === 0}
  <EmptyState title={t("search.resultsNone")} />
{:else if !search.searched}
  <p class="max-w-[40ch] text-body text-muted-foreground">{t("search.idleHint")}</p>
{:else}
  <DataTable {columns}>
    {#each search.results as item, index (search.keyOf(item))}
      {@const key = search.keyOf(item)}
      {@const playing = player.current?.id === key}
      <TrackRow
        {columns}
        {index}
        title={item.title ?? t("common.unknown")}
        artist={item.artist}
        subtitle={item.channel_title}
        duration={item.duration_sec}
        size={item.file_size}
        {playing}
        playLabel={search.playLabelOf(key)}
        onplay={() => void search.preview(item)}
        downloadLabel={t("search.download")}
        ondownload={() => void search.download(item)}
        cover={item.has_thumb ? { chatId: item.chat_id, messageId: item.message_id } : null}
      >
        {#snippet feedback()}
          {#if search.queued[key]}
            <Note tone="done">{t("search.queued")}</Note>
          {:else if search.rowError[key]}
            <Note tone="fail">{search.rowError[key]}</Note>
          {/if}
        {/snippet}
      </TrackRow>
    {/each}
  </DataTable>
{/if}
