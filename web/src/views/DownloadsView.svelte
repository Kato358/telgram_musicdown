<script lang="ts">
  /** 下载页（设计规范 §10，v3.8 改单栏）：绿色横幅（统计卡坐在里面）+ 下载队列 + 记录表卡。
   *
   * 事实只有两份、都在后端：`GET /api/history`（记录，分页/搜索/状态筛选都在服务端做）与
   * `GET /api/stats`（数字）；SSE 只当失效信号，实时字节走 `queue` 的进度帧。
   * 「正在跑的那些项」由队列卡负责（有控制、有读数），记录表因此不再重复状态与进度两列——
   * 记录的第二行说最该被看见的那件事：落盘路径 → 失败原因 → 灯 + 状态词。
   * 默认页签是「已入库」：这一页的主表就是曲库，进行中的项在队列卡里照看。
   */
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HardDriveIcon from "@lucide/svelte/icons/hard-drive";
  import MusicIcon from "@lucide/svelte/icons/music";
  import TriangleAlertIcon from "@lucide/svelte/icons/triangle-alert";
  import { api, errorText } from "$lib/api/client";
  import type { DownloadItemResult, HistoryRow } from "$lib/api/types";
  import { formatCount, formatSize } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { router } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { player, type Track } from "$lib/stores/player.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import type { Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
  } from "$lib/components/ui/dialog";
  import { Input } from "$lib/components/ui/input";
  import { Textarea } from "$lib/components/ui/textarea";
  import type { Column } from "$lib/components/app/DataTable.svelte";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import DownloadQueueCard from "$lib/components/app/DownloadQueueCard.svelte";
  import DownloadRow, { downloadColumns } from "$lib/components/app/DownloadRow.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import FilterTabs, { type TabItem } from "$lib/components/app/FilterTabs.svelte";
  import HeroBanner from "$lib/components/app/HeroBanner.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import StatCard from "$lib/components/app/StatCard.svelte";
  import type { TaskAction } from "$lib/components/app/TaskRow.svelte";

  /** 与后端 `list_history(limit=50)` 对齐：满页即说明可能还有下一页。 */
  const PAGE_SIZE = 50;

  /** 页签 → 服务端筛选值（`GET /api/history?status=`），一组状态就是逗号分隔的一串。 */
  const TAB_QUERY: Record<string, string> = {
    "": "",
    success: "success",
    active: "queued,downloading,paused",
    failed: "failed",
  };
  /** 进行中在队列卡里的先后：在传的排最前，其次已暂停，最后是还在等待的。 */
  const QUEUE_ORDER: Record<string, number> = { downloading: 0, paused: 1, queued: 2 };

  /** 站内链接带一个状态进来（最近入库、侧栏曲库卡给的是 `?status=success`），归到它所在的页签。 */
  function tabOf(status: string): string {
    if (status === "success" || status === "failed") return status;
    if (["queued", "downloading", "paused"].includes(status)) return "active";
    return "";
  }

  let links = $state("");
  let busy = $state(false);
  let notice = $state("");
  let failures = $state<DownloadItemResult[]>([]);
  let pasteOpen = $state(false);
  let clearOpen = $state(false);

  let keyword = $state("");
  let stableKeyword = $state("");
  // 默认页签是「已入库」（参考图的首屏）：这一页的主表就是曲库，进行中的项由队列卡照看。
  let tab = $state(tabOf(router.query.get("status") ?? "") || "success");
  let page = $state(0);
  let selected = $state<Set<number>>(new Set());

  let rows = $state<HistoryRow[]>([]);
  let loaded = $state(false);
  let error = $state("");
  let flash = $state<{ id: number; tone: Tone; text: string } | null>(null);

  /** 只认最后一次发出的请求，慢响应不会覆盖新筛选的结果。 */
  let requestSeq = 0;
  /** 已经消费过的 `?status=` 入参，避免同一个值反复触发。 */
  let handledStatus: string = router.query.get("status") ?? "";

  const columns = $derived(downloadColumns());
  const filtering = $derived(keyword.trim().length > 0 || tab !== "");
  const statusQuery = $derived(TAB_QUERY[tab] ?? "");
  const failedCount = $derived(stats.data?.library.failed ?? 0);
  /** 搜索框的占位词跟着页签说：默认的「已入库」页签照参考图指库，其余页签说筛选。 */
  const searchPlaceholder = $derived(
    tab === "success" ? t("downloads.searchLibrary") : t("downloads.searchPlaceholder"),
  );

  /** 页签的计数来自后端聚合：不数当前页（当前页只有 50 条，数出来会骗人）。
   *  顺序照参考图：已入库（默认）/ 全部 / 下载失败 / 进行中。参考图的第四个页签
   *  写的是「已完成」，但本系统里「完成」与「已入库」是同一批记录（成功即落盘），
   *  第四个页签保留「进行中」——四个页签四个不同的筛选，不摆两个一模一样的。 */
  const activeCount = $derived(
    (stats.data?.tasks.queued ?? 0) +
      (stats.data?.tasks.downloading ?? 0) +
      (stats.data?.tasks.paused ?? 0),
  );
  const tabs = $derived<TabItem[]>([
    { key: "success", label: t("downloads.statTracks"), count: stats.data?.library.tracks },
    { key: "", label: t("downloads.all") },
    { key: "failed", label: t("downloads.tabFailed"), count: stats.data?.library.failed },
    { key: "active", label: t("downloads.statActive"), count: activeCount },
  ]);

  /** 四张卡回答这一页的四件事：在做多少、失败多少、库里多少、占多大。
   *  提示行跟着状态说当下（参考图的画法）：在传说「正在下载」，只有等待说「在等待」，
   *  失败说有几项待重试——不是一张永远不变的说明牌。 */
  const cards = $derived.by(() => {
    const queued = stats.data?.tasks.queued ?? 0;
    const paused = stats.data?.tasks.paused ?? 0;
    const downloading = stats.data?.tasks.downloading ?? 0;
    const failed = stats.data?.library.failed ?? 0;
    return [
      {
        key: "active",
        label: t("downloads.statActive"),
        hint:
          downloading > 0
            ? t("downloads.statActiveLive")
            : activeCount > 0
              ? t("downloads.statActiveWaiting", { n: queued + paused })
              : t("downloads.statActiveHint"),
        value: formatCount(activeCount),
        tone: "primary" as const,
        icon: DownloadIcon,
      },
      {
        key: "failed",
        label: t("downloads.statFailed"),
        hint:
          failed > 0 ? t("downloads.statFailedSome", { n: failed }) : t("downloads.statFailedHint"),
        value: formatCount(failed),
        tone: "amber" as const,
        icon: TriangleAlertIcon,
      },
      {
        key: "tracks",
        label: t("downloads.statTracks"),
        hint: t("downloads.statTracksHint"),
        value: formatCount(stats.data?.library.tracks ?? null),
        tone: "blue" as const,
        icon: MusicIcon,
      },
      {
        key: "bytes",
        label: t("downloads.statBytes"),
        hint: t("downloads.statBytesHint"),
        value: formatSize(stats.data?.library.bytes ?? null),
        tone: "violet" as const,
        icon: HardDriveIcon,
      },
    ];
  });

  /** 队列卡与右栏进度卡说的是同一批任务，顺序也一样。 */
  const activeTasks = $derived(
    queue.tasks
      .filter((task) => task.status in QUEUE_ORDER)
      .sort((a, b) => (QUEUE_ORDER[a.status] ?? 9) - (QUEUE_ORDER[b.status] ?? 9) || a.id - b.id),
  );

  const selectedRows = $derived(rows.filter((row) => selected.has(row.id)));
  const playableSelected = $derived(selectedRows.filter((row) => row.save_path !== null));
  const allSelected = $derived(rows.length > 0 && rows.every((row) => selected.has(row.id)));
  const someSelected = $derived(selected.size > 0 && !allSelected);
  /** 页脚里的一页总大小：只加这一页有大小可算的行。 */
  const pageBytes = $derived(rows.reduce((sum, row) => sum + (row.file_size ?? 0), 0));

  const playable = $derived(rows.filter((row) => row.save_path !== null));
  const tracks = $derived(playable.map(rowToTrack));

  function rowToTrack(row: HistoryRow): Track {
    return {
      id: String(row.id),
      title: row.title ?? "",
      artist: row.artist,
      streamUrl: `/api/history/${row.id}/stream`,
    };
  }

  async function load(q: string, status: string, pg: number) {
    const seq = (requestSeq += 1);
    const parts: string[] = [];
    if (q) parts.push(`q=${encodeURIComponent(q)}`);
    if (status) parts.push(`status=${encodeURIComponent(status)}`);
    if (pg > 0) parts.push(`page=${pg}`);
    const query = parts.join("&");
    try {
      const next = await api.get<HistoryRow[]>(query ? `/api/history?${query}` : "/api/history");
      if (seq !== requestSeq) return;
      rows = next;
      error = "";
    } catch (err) {
      if (seq !== requestSeq) return;
      error = errorText(err, t("common.error"));
    } finally {
      if (seq === requestSeq) loaded = true;
    }
  }

  /** 动作之后列表、队列快照与统计卡一起重取：三者说的是同一批下载。 */
  async function refresh() {
    await Promise.all([
      load(stableKeyword.trim(), statusQuery, page),
      queue.refresh(),
      stats.refresh(),
    ]);
  }

  // 关键词去抖：停顿 250ms 后稳定下来的值才触发重取，同时回到第一页。
  $effect(() => {
    const next = keyword;
    const timer = setTimeout(() => {
      stableKeyword = next;
      page = 0;
      selected = new Set();
    }, 250);
    return () => clearTimeout(timer);
  });

  // 筛选条件（去抖后的关键词、页签、页码）任一变化都重取。
  $effect(() => {
    const q = stableKeyword.trim();
    const status = statusQuery;
    const pg = page;
    void load(q, status, pg);
  });

  // 状态事件只当失效信号：行的状态与落盘路径由 DB 说了算，实时字节另走 SSE 进度帧。
  $effect(() => events.onStatus(() => void load(stableKeyword.trim(), statusQuery, page)));

  // 站内入口带 `?status=` 进来（最近入库、侧栏曲库卡）：新值消费一次；不带参数的进入
  // （导航到「下载」）把筛选放回默认的「已入库」，免得地址栏与列表各说各话。
  $effect(() => {
    const next = router.query.get("status") ?? "";
    if (next === handledStatus) return;
    handledStatus = next;
    tab = tabOf(next) || "success";
    page = 0;
    selected = new Set();
  });

  function setTab(next: string) {
    if (next === tab) return;
    tab = next;
    page = 0;
    selected = new Set();
  }

  function toggleAll(checked: boolean) {
    selected = checked ? new Set(rows.map((row) => row.id)) : new Set();
  }

  function toggleRow(row: HistoryRow, checked: boolean) {
    // 换一个新的 Set 而不是改旧的：状态是 `$state` 的普通 Set，就地 add/delete 不触发更新。
    selected = checked
      ? new Set([...selected, row.id])
      : new Set([...selected].filter((id) => id !== row.id));
  }

  function playFrom(row: HistoryRow) {
    const index = playable.findIndex((item) => item.id === row.id);
    if (index < 0) return;
    player.play(tracks, index);
  }

  function playSelected() {
    const next = playableSelected.map(rowToTrack);
    if (next.length === 0) return;
    player.play(next, 0);
  }

  async function act(taskId: number, action: TaskAction) {
    error = "";
    try {
      await api.post(`/api/downloads/${taskId}/${action}`);
      await refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  /** 重试：台账还在就复位那条任务，台账被删过（记录比它活得久）就按消息重新入队。 */
  async function retry(row: HistoryRow) {
    flash = null;
    try {
      if (row.task_id !== null) {
        await api.post(`/api/downloads/${row.task_id}/retry`);
      } else {
        await enqueueRefs([row]);
      }
      flash = { id: row.id, tone: "done", text: t("downloads.redownloaded") };
      await refresh();
    } catch (err) {
      flash = { id: row.id, tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  async function redownload(row: HistoryRow) {
    flash = null;
    try {
      await enqueueRefs([row]);
      flash = { id: row.id, tone: "done", text: t("downloads.redownloaded") };
      await refresh();
    } catch (err) {
      flash = { id: row.id, tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  function enqueueRefs(targets: HistoryRow[]) {
    return api.post<{ items: DownloadItemResult[] }>("/api/downloads", {
      message_refs: targets.map((row) => ({ chat_id: row.chat_id, message_id: row.message_id })),
    });
  }

  /** 批量重下：一次请求带全部勾选行（后端按 message_refs 逐条入队）。 */
  async function redownloadSelected() {
    if (selectedRows.length === 0) return;
    error = "";
    notice = "";
    try {
      const resp = await enqueueRefs(selectedRows);
      const rejected = resp.items.filter((item) => item.error !== undefined);
      notice = t("downloads.redownloadQueued", { n: resp.items.length - rejected.length });
      if (rejected.length > 0) {
        notice = `${notice} · ${t("downloads.enqueueErrors", { n: rejected.length })}`;
      }
      selected = new Set();
      await refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  async function copyPath(row: HistoryRow) {
    const path = row.save_path;
    if (!path) return;
    flash = null;
    try {
      await navigator.clipboard.writeText(path);
      flash = { id: row.id, tone: "done", text: t("downloads.copied") };
    } catch (err) {
      flash = { id: row.id, tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  async function enqueue() {
    const urls = links
      .split(/[\s,]+/)
      .map((value) => value.trim())
      .filter((value) => value.length > 0);
    if (urls.length === 0) return;
    busy = true;
    error = "";
    notice = "";
    failures = [];
    try {
      const resp = await api.post<{ items: DownloadItemResult[] }>("/api/downloads", { urls });
      const rejected = resp.items.filter((item) => item.error !== undefined);
      failures = rejected;
      notice = t("downloads.enqueueResult", { n: resp.items.length - rejected.length });
      if (rejected.length > 0) {
        notice = `${notice} · ${t("downloads.enqueueErrors", { n: rejected.length })}`;
      }
      links = "";
      await refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    } finally {
      busy = false;
    }
  }

  /** 清空队列 = 取消还在等待的那些（正在下载的那一项不动）：逐条走既有的取消端点，
   *  失败的那几条如实报出来，不假装整批都成了。 */
  async function clearQueue() {
    const targets = queue.queued.map((task) => task.id);
    clearOpen = false;
    if (targets.length === 0) return;
    busy = true;
    error = "";
    notice = "";
    const results = await Promise.allSettled(
      targets.map((id) => api.post(`/api/downloads/${id}/cancel`)),
    );
    const failed = results.filter((result) => result.status === "rejected").length;
    notice =
      failed === 0
        ? t("downloads.clearResult", { n: targets.length })
        : `${t("downloads.clearResult", { n: targets.length - failed })} · ${t("common.error")}`;
    busy = false;
    await refresh();
  }

  async function retryFailed() {
    error = "";
    notice = "";
    failures = [];
    try {
      const resp = await api.post<{ retried: number }>("/api/downloads/retry-failed");
      notice = t("downloads.retryResult", { n: resp.retried });
      await refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }
</script>

<div class="flex min-w-0 flex-col gap-4">
  {#snippet statGrid()}
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {#each cards as card (card.key)}
        <StatCard
          label={card.label}
          value={card.value}
          hint={card.hint}
          tone={card.tone}
          icon={card.icon}
        />
      {/each}
    </div>
  {/snippet}

  <HeroBanner title={t("downloads.heroTitle")} body={t("downloads.heroBody")}>
    {@render statGrid()}
  </HeroBanner>

  <div id="queue">
    <DownloadQueueCard
      tasks={activeTasks}
      queued={queue.queued.length}
      readings={(task) => queue.readings(task)}
      onact={(taskId, action) => void act(taskId, action)}
      onadd={() => (pasteOpen = true)}
      onclear={() => (clearOpen = true)}
    />
  </div>

  {#if error}
    <Note tone="fail">{error}</Note>
  {/if}
  {#if notice}
    <Note tone="done">{notice}</Note>
  {/if}

  {#snippet tableHeader()}
    <FilterTabs
      items={tabs}
      value={tab}
      onchange={setTab}
      label={t("downloads.tabsLabel")}
      class="min-w-0 flex-1"
    />
    {#if failedCount > 0}
      <Button variant="outline" size="sm" onclick={() => void retryFailed()}>
        {t("downloads.retryFailed")}
      </Button>
    {/if}
    <Input
      bind:value={keyword}
      type="search"
      class="w-full rounded-full sm:w-48"
      placeholder={searchPlaceholder}
      aria-label={searchPlaceholder}
    />
  {/snippet}

  {#snippet selectionBar()}
    <div class="flex flex-wrap items-center gap-2">
      <span class="tabular text-caption text-primary">
        {t("downloads.selectedCount", { n: selected.size })}
      </span>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <Button variant="outline" size="xs" onclick={() => (selected = new Set())}>
          {t("downloads.clearSelection")}
        </Button>
        <Button
          variant="outline"
          size="xs"
          disabled={playableSelected.length === 0}
          onclick={playSelected}
        >
          {t("downloads.playSelected")}
        </Button>
        <Button size="xs" onclick={() => void redownloadSelected()}>
          {t("downloads.redownloadSelected")}
        </Button>
      </div>
    </div>
  {/snippet}

  {#snippet tableFooter()}
    <span class="tabular text-caption text-muted-foreground">
      {#if pageBytes > 0}
        {t("downloads.foundSize", { n: rows.length, size: formatSize(pageBytes) })}
      {:else}
        {t("downloads.found", { n: rows.length })}
      {/if}
    </span>
    {#if page > 0 || rows.length >= PAGE_SIZE}
      <div class="ml-auto flex items-center gap-1">
        <button
          type="button"
          class="ui-transition grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={t("downloads.pagePrev")}
          disabled={page === 0}
          onclick={() => {
            page -= 1;
            selected = new Set();
          }}
        >
          <ChevronLeftIcon class="size-4" aria-hidden="true" />
        </button>
        <span
          class="tabular grid size-8 place-items-center rounded-control border border-border text-caption"
          aria-current="page"
        >
          {page + 1}
        </span>
        <button
          type="button"
          class="ui-transition grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={t("downloads.pageNext")}
          disabled={rows.length < PAGE_SIZE}
          onclick={() => {
            page += 1;
            selected = new Set();
          }}
        >
          <ChevronRightIcon class="size-4" aria-hidden="true" />
        </button>
      </div>
    {/if}
  {/snippet}

  {#snippet tableHeaderCell(column: Column)}
    {#if column.key === "check"}
      <span class={column.class}>
        <Checkbox
          checked={allSelected}
          indeterminate={someSelected}
          onCheckedChange={(value) => toggleAll(value === true)}
          aria-label={t("downloads.selectAll")}
        />
      </span>
    {:else}
      <span class={column.class}>{column.label}</span>
    {/if}
  {/snippet}

  <DataTable
    {columns}
    headerCell={tableHeaderCell}
    header={tableHeader}
    toolbar={selected.size > 0 ? selectionBar : undefined}
    footer={tableFooter}
  >
    {#if !loaded}
      <li class="px-4 py-8 text-caption text-muted-foreground md:px-6">
        {t("common.loading")}
      </li>
    {:else if rows.length === 0 && !error}
      <li class="px-4 py-4 md:px-6">
        <EmptyState bare title={filtering ? t("downloads.none") : t("downloads.empty")}>
          {#snippet actions()}
            <Button onclick={() => (pasteOpen = true)}>{t("downloads.addLink")}</Button>
          {/snippet}
        </EmptyState>
      </li>
    {:else}
      {#each rows as row (row.id)}
        {@const playing = player.current?.id === String(row.id)}
        {@const rowFeedback = flash && flash.id === row.id ? flash : null}
        <DownloadRow
          {columns}
          {row}
          readings={row.task_id !== null ? queue.readingsFor(row.task_id) : null}
          {playing}
          selected={selected.has(row.id)}
          playLabel={playing ? t("downloads.playing") : t("downloads.play")}
          onselected={(checked) => toggleRow(row, checked)}
          onplay={() => playFrom(row)}
          onretry={() => void retry(row)}
          onredownload={() => void redownload(row)}
          oncopy={() => void copyPath(row)}
        >
          {#snippet feedback()}
            {#if rowFeedback}
              <Note tone={rowFeedback.tone}>{rowFeedback.text}</Note>
            {/if}
          {/snippet}
        </DownloadRow>
      {/each}
    {/if}
  </DataTable>
</div>

<Dialog bind:open={pasteOpen}>
  <DialogContent>
    <DialogHeader>
      <DialogTitle class="text-h2 font-semibold">{t("downloads.pasteLabel")}</DialogTitle>
      <DialogDescription class="text-caption">{t("downloads.pasteHint")}</DialogDescription>
    </DialogHeader>

    <div class="flex flex-col gap-3">
      <Textarea
        id="download-links"
        bind:value={links}
        rows={4}
        class="text-code"
        placeholder={t("downloads.pastePlaceholder")}
        aria-label={t("downloads.pasteLabel")}
      />
      {#if notice}
        <Note tone={failures.length > 0 ? "wait" : "done"}>{notice}</Note>
      {/if}
      {#each failures as item (item.url ?? `${item.chat_id}-${item.message_id}`)}
        <Note tone="fail">{item.url}：{item.error}</Note>
      {/each}
    </div>

    <DialogFooter>
      <Button variant="outline" onclick={() => (pasteOpen = false)}>{t("common.cancel")}</Button>
      <Button size="lg" disabled={busy || links.trim().length === 0} onclick={() => void enqueue()}>
        {busy ? t("downloads.enqueuing") : t("downloads.enqueue")}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>

<Dialog bind:open={clearOpen}>
  <DialogContent>
    <DialogHeader>
      <DialogTitle class="text-h2 font-semibold">{t("downloads.clearTitle")}</DialogTitle>
      <DialogDescription class="text-caption">
        {t("downloads.clearBody", { n: queue.queued.length })}
      </DialogDescription>
    </DialogHeader>

    <DialogFooter>
      <Button variant="outline" onclick={() => (clearOpen = false)}>{t("common.cancel")}</Button>
      <Button variant="destructive" size="lg" disabled={busy} onclick={() => void clearQueue()}>
        {t("downloads.clearConfirm")}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
