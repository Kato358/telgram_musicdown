<script lang="ts">
  /** 搜索（设计规范 §10）：逐源模式在已启用音乐源内搜，全账号模式搜账号加入的全部对话
   *  （FR-SEARCH-01）；两种模式都带上已启用的在线源平台——它们是搜索范围那一行的药丸，
   *  全账号模式下这一行只剩它们。
   *
   * 状态都在模块级 `search` store（lib/stores/search.svelte.ts），切页不丢：
   * 本组件只是视图层，回到搜索页时上次的关键词和结果列表原样还在。
   * 顶栏搜索通过 `?q=` 进入本页，入参只在变化时消费一次。
   */
  import { onMount } from "svelte";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf, router } from "$lib/router.svelte";
  import { search, SORT_OPTIONS } from "$lib/stores/search.svelte";
  import { player } from "$lib/stores/player.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import type { Column } from "$lib/components/app/DataTable.svelte";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import QualityPickerDialog from "$lib/components/app/QualityPickerDialog.svelte";
  import SectionCard from "$lib/components/app/SectionCard.svelte";
  import TrackRow, { COL_CHECK, trackColumns } from "$lib/components/app/TrackRow.svelte";

  /** 多选时在列首拼一列勾选框，退出多选即摘掉。 */
  const columns = $derived<Column[]>(
    search.selectMode
      ? [{ key: "check", label: "", class: COL_CHECK }, ...trackColumns()]
      : trackColumns(),
  );
  const resultsCount = $derived(search.results.length);

  /** 搜索范围一行的候选：逐源模式是全部可搜源；全账号模式只剩在线源——频道不在全账号
   *  链路里（勾了也搜不到），而在线平台会被追加上扇出，所以它们留在这行当筛选。 */
  const scopeOptions = $derived(session.globalSearch ? search.onlineSources : search.sources);

  /** 批量下载的起点：「下载所选」按钮的中心，整批飞片从这里级联起飞。 */
  function downloadSelected(event: MouseEvent) {
    const target = event.currentTarget as HTMLElement | null;
    if (target === null) return;
    const rect = target.getBoundingClientRect();
    void search.downloadSelected({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  }

  $effect(() => {
    const keyword = router.query.get("q")?.trim() ?? "";
    if (keyword.length === 0 || keyword === search.query.trim()) return;
    search.query = keyword;
    void search.runSearch();
  });

  onMount(() => {
    // 两种模式都要这份清单：全账号模式下它只剩在线源，但那几颗药丸正是筛选入口。
    void search.loadSources();
  });
</script>

<PageHeader
  title={t("search.title")}
  lede={session.globalSearch ? t("search.ledeGlobal") : t("search.lede")}
>
  {#snippet aside()}
    {#if search.searched && search.results.length > 0}
      <span class="tabular text-caption text-muted-foreground">
        {t("search.resultsCount", { n: resultsCount })}
      </span>
      <Button variant="outline" size="sm" onclick={() => search.toggleSelectMode()}>
        {search.selectMode ? t("search.exitSelect") : t("search.selectMode")}
      </Button>
    {/if}
  {/snippet}
</PageHeader>

{#if !session.globalSearch && search.sources.length === 0}
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

    {#snippet pill(active: boolean, label: string, onclick: () => void, sub?: string)}
      <button
        type="button"
        class="ui-transition inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1 text-caption {active
          ? 'bg-primary-soft text-primary'
          : 'border border-border text-muted-foreground hover:bg-rule hover:text-foreground'}"
        aria-pressed={active}
        {onclick}
      >
        <span>{label}</span>
        {#if sub}
          <span class="text-code">{sub}</span>
        {/if}
      </button>
    {/snippet}

    {#if scopeOptions.length > 0}
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-caption text-muted-foreground">{t("search.filterSources")}</span>
        {@render pill(
          search.activeSourceIds === undefined,
          session.globalSearch ? t("search.allSourcesGlobal") : t("search.allSources"),
          () => (search.selected = []),
        )}
        {#each scopeOptions as source (source.id)}
          {@render pill(
            search.selected.includes(source.id),
            source.title,
            () => search.toggleSource(source.id),
            source.online ? t("search.onlineBy") : undefined,
          )}
        {/each}
      </div>
    {/if}

    <div class="flex flex-wrap items-center gap-2">
      <span class="text-caption text-muted-foreground">{t("search.sortLabel")}</span>
      {#each SORT_OPTIONS as option (option)}
        {@render pill(search.sort === option, t(`search.sort.${option}`), () =>
          search.setSort(option),
        )}
      {/each}
    </div>
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

{#if search.partial}
  <Note tone="wait">
    <span class="block">{t("search.partial")}</span>
    {#if search.pendingTitles.length > 0}
      <span class="block text-caption text-muted-foreground">
        {search.pendingTitles.join(t("search.listSep"))}
      </span>
    {/if}
  </Note>
{/if}

{#if session.globalSearch}
  <Note>{t("search.globalScope")}</Note>
{/if}

{#if search.needSources && !session.globalSearch}
  <Note tone="wait">{t("search.needSources")}</Note>
{:else if search.searched && search.results.length === 0}
  <EmptyState
    title={session.globalSearch ? t("search.resultsNoneGlobal") : t("search.resultsNone")}
  />
{:else if search.searched}
  {#snippet selectBar()}
    <div class="flex flex-wrap items-center gap-2">
      <span class="tabular text-caption text-primary">
        {t("search.selectedCount", { n: search.selection.size })}
      </span>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <Button variant="outline" size="xs" onclick={() => (search.selection = new Set())}>
          {t("search.clearSelection")}
        </Button>
        <Button size="xs" disabled={search.selection.size === 0} onclick={downloadSelected}>
          {t("search.downloadSelected")}
        </Button>
      </div>
    </div>
  {/snippet}

  {#snippet headerCell(column: Column)}
    {#if column.key === "check"}
      <span class={column.class}>
        <Checkbox
          checked={search.allSelected}
          indeterminate={search.someSelected}
          onCheckedChange={(value) => search.toggleSelectAll(value === true)}
          aria-label={t("search.selectAll")}
        />
      </span>
    {:else}
      <span class={column.class}>{column.label}</span>
    {/if}
  {/snippet}

  {#snippet footerBar()}
    <span class="tabular text-caption text-muted-foreground">
      {t("search.moreHint", { n: search.results.length })}
    </span>
    {#if search.hasMore}
      <div class="ml-auto">
        <Button
          variant="outline"
          size="xs"
          disabled={search.loadingMore}
          onclick={() => void search.loadMore()}
        >
          {search.loadingMore ? t("search.loadingMore") : t("search.loadMore")}
        </Button>
      </div>
    {/if}
  {/snippet}

  <DataTable
    {columns}
    {headerCell}
    footer={footerBar}
    toolbar={search.selectMode ? selectBar : undefined}
  >
    {#each search.results as item, index (search.keyOf(item))}
      {@const key = search.keyOf(item)}
      {@const playing = player.isPlaying(key)}
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
        ondownload={(origin) => void search.requestDownload(item, origin)}
        online={search.isOnline(item)}
        selected={search.selection.has(key)}
        onselected={(checked) => search.toggleSelect(key, checked)}
        selectLabel={t("search.selectRow", { title: item.title ?? t("common.unknown") })}
        cover={{ title: item.title, artist: item.artist }}
      >
        {#snippet feedback()}
          {#if search.rowError[key]}
            <Note tone="fail">{search.rowError[key]}</Note>
          {/if}
        {/snippet}
      </TrackRow>
    {/each}
  </DataTable>
{/if}

<QualityPickerDialog onconfirm={() => void search.confirmQuality()} />
