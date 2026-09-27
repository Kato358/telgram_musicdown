<script lang="ts">
  /** 本地曲库页（FR-LIB）：downloads 落盘文件的扫描台账。
   *
   * 事实只有一份、在后端：`GET /api/local-library`（筛选/排序/分页都在服务端做，
   * 懒加载每次只取一页——曲库上万首也不会把首屏拖慢）。扫描完成后端经 SSE
   * `library.scan` 推送，这里观察 events.libraryRevision 重取已加载的部分；
   * 下载完成的 success 事件会借「延迟触发重扫」把新文件带进曲库。
   * 文件在曲库外被删的行保留（missing），挂着下载记录的可以一键重新入队；
   * 页面上的「删除」是删磁盘文件（连记录一起删），删完这条行不再回来。
   */
  import { onMount } from "svelte";
  import { untrack } from "svelte";
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import MusicIcon from "@lucide/svelte/icons/music";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import { api, errorText } from "$lib/api/client";
  import { fetchAllLocalTracks, fetchLocalPage, LIBRARY_PAGE_SIZE } from "$lib/api/library";
  import type { LocalLibraryResponse, LocalTrackRow } from "$lib/api/types";
  import { formatSize } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { player } from "$lib/stores/player.svelte";
  import type { LibraryFilter } from "$lib/api/library";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
  } from "$lib/components/ui/dialog";
  import DataTable, { type Column } from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import FilterTabs, { type TabItem } from "$lib/components/app/FilterTabs.svelte";
  import LibraryRow, { libraryColumns } from "$lib/components/app/LibraryRow.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

  /** 与后端 MAX_LIMIT 对齐：拉全（播放上下文）与重取已加载段都用 200 的块。 */
  const CHUNK = 200;

  const columns = $derived(libraryColumns());

  let keyword = $state("");
  let stableKeyword = $state("");
  /** "" = 全部 | "present" | "missing"。 */
  let tab = $state("");
  /** 表头排序（Excel 式：点列名升序，再点降序）；默认入库时间最新在前。 */
  let sort = $state("created");
  let order = $state<"asc" | "desc">("desc");

  /** 表格列 → 服务端排序键（date 列的排序事实是 first_seen_at）。 */
  const SORT_KEY_BY_COLUMN: Record<string, string> = {
    title: "title",
    artist: "artist",
    album: "album",
    duration: "duration",
    size: "size",
    bitrate: "bitrate",
    date: "created",
  };

  let items = $state<LocalTrackRow[]>([]);
  let total = $state(0);
  let counts = $state({ present: 0, missing: 0, all: 0 });
  let bytes = $state(0);

  let loading = $state(false);
  let loadingMore = $state(false);
  let loaded = $state(false);
  let error = $state("");
  let notice = $state("");
  let flash = $state<{ id: number; text: string } | null>(null);
  let rescanning = $state(false);

  let confirmOpen = $state(false);
  let deleteTarget = $state<LocalTrackRow | null>(null);

  /** 只认最后一次发出的请求：慢响应不会覆盖新筛选的结果。 */
  let requestSeq = 0;
  /** 当前生效的筛选快照（普通变量）：loadMore / 事件重取读它，不进响应式依赖。 */
  let activeFilter: LibraryFilter = { sort: "created", order: "desc" };

  const missingParam = $derived(
    tab === "present" ? "0" : tab === "missing" ? "1" : "",
  );
  const filter = $derived<LibraryFilter>({
    q: stableKeyword.trim() || undefined,
    missing: missingParam || undefined,
    sort,
    order,
  });
  const filtering = $derived(Boolean(filter.q || filter.missing));

  const tabs = $derived<TabItem[]>([
    { key: "", label: t("library.tabAll"), count: counts.all },
    { key: "present", label: t("library.tabPresent"), count: counts.present },
    { key: "missing", label: t("library.tabMissing"), count: counts.missing },
  ]);

  /** Excel 式表头排序：同一列再点翻转方向，换列回到升序起步。 */
  function toggleSort(key: string) {
    if (sort === key) {
      order = order === "asc" ? "desc" : "asc";
    } else {
      sort = key;
      order = "asc";
    }
  }

  function applyData(data: LocalLibraryResponse, replace: boolean) {
    total = data.total;
    counts = data.counts;
    bytes = data.bytes;
    items = replace ? data.items : [...items, ...data.items];
  }

  // 筛选条件任一变化：重置列表并拉第一页（服务端筛选/排序，前端只管翻页）。
  $effect(() => {
    const next = filter;
    activeFilter = next;
    const seq = (requestSeq += 1);
    loading = true;
    loaded = false;
    items = [];
    total = 0;
    void (async () => {
      try {
        const data = await fetchLocalPage(next, 0, LIBRARY_PAGE_SIZE);
        if (seq !== requestSeq) return;
        applyData(data, true);
        error = "";
      } catch (err) {
        if (seq !== requestSeq) return;
        error = errorText(err, t("common.error"));
      } finally {
        if (seq === requestSeq) {
          loading = false;
          loaded = true;
        }
      }
    })();
  });

  // 关键词去抖：停顿 250ms 后稳定下来的值才进筛选。
  $effect(() => {
    const next = keyword;
    const timer = setTimeout(() => (stableKeyword = next), 250);
    return () => clearTimeout(timer);
  });

  /** 触底追加一页：未加载完且没有在途请求时才发。 */
  function loadMore() {
    if (loading || loadingMore || !loaded) return;
    if (items.length >= total) return;
    loadingMore = true;
    const seq = requestSeq;
    const offset = items.length;
    void (async () => {
      try {
        const data = await fetchLocalPage(activeFilter, offset, LIBRARY_PAGE_SIZE);
        if (seq !== requestSeq) return;
        applyData(data, false);
        error = "";
      } catch (err) {
        if (seq !== requestSeq) return;
        error = errorText(err, t("common.error"));
      } finally {
        if (seq === requestSeq) loadingMore = false;
      }
    })();
  }

  /** 无限滚动的哨兵：靠近列表底部 300px 就预先取下一页。 */
  let sentinel = $state<HTMLElement | undefined>();
  $effect(() => {
    const el = sentinel;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  });

  /** 重取已加载的行数（重扫完成后列表可能变了；按页取整，滚动位置自然保持）。 */
  async function refreshLoaded() {
    const keep = Math.max(
      LIBRARY_PAGE_SIZE,
      Math.ceil(items.length / LIBRARY_PAGE_SIZE) * LIBRARY_PAGE_SIZE,
    );
    const fresh: LocalTrackRow[] = [];
    let last: LocalLibraryResponse | null = null;
    let offset = 0;
    while (offset < keep) {
      // 按需取：最后一段可能是半块，不给「刷新一次反而拉全库」留口子
      const data = await fetchLocalPage(activeFilter, offset, Math.min(CHUNK, keep - offset));
      last = data;
      fresh.push(...data.items);
      offset += Math.min(CHUNK, keep - offset);
      if (offset >= data.total) break;
    }
    if (last) applyData({ ...last, items: fresh }, true);
    error = "";
  }

  /** 重扫完成后（SSE）：显示结果提示并重取已加载段。 */
  $effect(() => {
    const rev = events.libraryRevision;
    if (rev === 0) return;
    const scan = events.lastLibraryScan;
    rescanning = false;
    if (scan) {
      notice = t("library.rescanDone", {
        added: scan.added,
        updated: scan.updated,
        missing: scan.missing,
      });
    }
    void untrack(refreshLoaded);
  });

  async function triggerScan() {
    if (rescanning) return;
    rescanning = true;
    try {
      await api.post("/api/local-library/scan");
    } catch (err) {
      rescanning = false;
      error = errorText(err, t("common.error"));
    }
  }

  onMount(() => {
    void triggerScan(); // 进页先重扫一次：手动放进 downloads 的文件也能立刻入库
    // 下载完成的批次完成后延迟重扫，把新文件带进曲库（done 帧再驱动列表刷新）
    let scanTimer: ReturnType<typeof setTimeout> | undefined;
    const off = events.onStatus((event) => {
      if (event.status !== "success") return;
      clearTimeout(scanTimer);
      scanTimer = setTimeout(() => void triggerScan(), 2_500);
    });
    return () => {
      off();
      clearTimeout(scanTimer);
    };
  });

  async function playFrom(row: LocalTrackRow) {
    error = "";
    try {
      const tracks = await fetchAllLocalTracks(activeFilter);
      const index = tracks.findIndex((track) => track.id === `local-${row.id}`);
      if (index >= 0) player.play(tracks, index);
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  /** 文件被删但记录还挂着下载记录 → 按消息重新入队。 */
  async function redownload(row: LocalTrackRow) {
    if (row.chat_id === null || row.message_id === null) return;
    flash = null;
    try {
      await api.post("/api/downloads", {
        message_refs: [{ chat_id: row.chat_id, message_id: row.message_id }],
      });
      flash = { id: row.id, text: t("library.redownloaded") };
    } catch (err) {
      flash = { id: row.id, text: errorText(err, t("common.error")) };
    }
  }

  function askDelete(row: LocalTrackRow) {
    deleteTarget = row;
    confirmOpen = true;
  }

  async function confirmDelete() {
    const row = deleteTarget;
    confirmOpen = false;
    deleteTarget = null;
    if (row === null) return;
    try {
      await api.delete(`/api/local-library/${row.id}`);
      notice = t(row.missing ? "library.deletedRecord" : "library.deleted");
      await refreshLoaded();
    } catch (err) {
      flash = { id: row.id, text: errorText(err, t("common.error")) };
    }
  }
</script>

<div class="flex min-w-0 flex-col gap-4">
  <PageHeader title={t("library.title")} lede={t("library.lede")}>
    {#snippet aside()}
      <span class="tabular hidden text-caption text-muted-foreground sm:inline">
        {t("library.summaryPresent", { present: counts.present })}
        {#if counts.missing > 0}
          · {t("library.summaryMissing", { missing: counts.missing })}
        {/if}
        · {formatSize(bytes)}
      </span>
      <Button
        variant="outline"
        size="sm"
        disabled={rescanning}
        onclick={() => void triggerScan()}
      >
        <RefreshCwIcon class="size-4 {rescanning ? 'animate-spin' : ''}" aria-hidden="true" />
        {rescanning ? t("common.loading") : t("library.rescan")}
      </Button>
    {/snippet}
  </PageHeader>

  {#if error}
    <Note tone="fail">{error}</Note>
  {/if}
  {#if notice}
    <Note tone="done">{notice}</Note>
  {/if}

  {#snippet tableToolbar()}
    <div class="flex flex-wrap items-center gap-2">
      <FilterTabs
        items={tabs}
        value={tab}
        onchange={(next) => (tab = next)}
        label={t("library.tabsLabel")}
        class="min-w-0 flex-1"
      />
      <Input
        bind:value={keyword}
        type="search"
        class="w-full rounded-full sm:w-52"
        placeholder={t("library.searchPlaceholder")}
        aria-label={t("library.searchPlaceholder")}
      />
    </div>
  {/snippet}

  {#snippet tableHeaderCell(column: Column)}
    {@const sortKey = SORT_KEY_BY_COLUMN[column.key]}
    {#if sortKey}
      <!-- Excel 式表头排序：列名即排序钮，当前列带箭头指示方向 -->
      <button
        type="button"
        class="ui-transition {column.class} {sort === sortKey
          ? 'text-primary'
          : 'hover:text-foreground'}"
        onclick={() => toggleSort(sortKey)}
      >
        <span class="inline-flex items-center gap-1">
          {column.label}
          {#if sort === sortKey}
            {#if order === "asc"}
              <ArrowUpIcon class="size-3" aria-hidden="true" />
            {:else}
              <ArrowDownIcon class="size-3" aria-hidden="true" />
            {/if}
          {/if}
        </span>
      </button>
    {:else}
      <span class={column.class}>{column.label}</span>
    {/if}
  {/snippet}

  {#snippet tableFooter()}
    <span class="tabular text-caption text-muted-foreground">
      {#if items.length < total}
        {t("library.loadedCount", { m: items.length, n: total })}
      {:else if total > 0}
        {t("library.loadedAll", { n: total })}
      {/if}
    </span>
  {/snippet}

  <DataTable
    {columns}
    toolbar={tableToolbar}
    headerCell={tableHeaderCell}
    footer={tableFooter}
  >
    {#if !loaded}
      <li class="px-4 py-8 text-caption text-muted-foreground md:px-6">
        {t("common.loading")}
      </li>
    {:else if items.length === 0 && !error}
      <li class="px-4 py-4 md:px-6">
        <EmptyState bare icon={MusicIcon} title={filtering ? t("library.none") : t("library.empty")} />
      </li>
    {:else}
      {#each items as row (row.id)}
        {@const playing = player.current?.id === `local-${row.id}`}
        <LibraryRow
          {columns}
          {row}
          {playing}
          playLabel={playing ? t("library.playing") : t("library.play")}
          feedback={flash && flash.id === row.id ? flash.text : null}
          onplay={() => void playFrom(row)}
          onredownload={() => void redownload(row)}
          ondelete={() => askDelete(row)}
        />
      {/each}
    {/if}
  </DataTable>

  <!-- 懒加载哨兵：贴在表格之后，靠近它就取下一页 -->
  <div bind:this={sentinel} class="h-1" aria-hidden="true">
    {#if loadingMore}
      <p class="py-2 text-center text-caption text-muted-foreground">
        {t("library.loadingMore")}
      </p>
    {/if}
  </div>
</div>

<Dialog
  bind:open={confirmOpen}
  onOpenChange={(open) => {
    if (!open) deleteTarget = null;
  }}
>
  <DialogContent>
    <DialogHeader>
      <DialogTitle class="text-h2 font-semibold">
        {deleteTarget
          ? t("library.deleteTitle", {
              title: deleteTarget.title?.trim() || deleteTarget.file_name,
            })
          : ""}
      </DialogTitle>
      <DialogDescription class="text-caption">
        {#if deleteTarget}
          {t(deleteTarget.missing ? "library.deleteRecordBody" : "library.deleteBody", {
            title: deleteTarget.title?.trim() || deleteTarget.file_name,
          })}
        {/if}
      </DialogDescription>
    </DialogHeader>

    <DialogFooter>
      <Button variant="outline" onclick={() => (confirmOpen = false)}>
        {t("common.cancel")}
      </Button>
      <Button variant="destructive" size="lg" onclick={() => void confirmDelete()}>
        {t("library.deleteConfirm")}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
