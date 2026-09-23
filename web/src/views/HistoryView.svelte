<script lang="ts">
  /** 下载历史（设计规范 §10）：已入库曲目与它们的落盘位置。
   *
   * 落盘路径是这一页的主信息面，所以它是每行的第二行（等宽、可悬停看全）；
   * 列表事实源是后端分页结果（`/api/history`，DB 才是唯一事实源），
   * 关键词做 250ms 去抖后再重取，避免每个按键都打一次接口。
   */
  import { api, errorText } from "$lib/api/client";
  import type { DownloadItemResult, HistoryRow } from "$lib/api/types";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { t } from "$lib/i18n/index.svelte";
  import { player, type Track } from "$lib/stores/player.svelte";
  import { statusText, taskTone, type Tone } from "$lib/tone";
  import { Input } from "$lib/components/ui/input";
  import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
  } from "$lib/components/ui/select";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import TrackRow, { trackColumns, type RowMenuItem } from "$lib/components/app/TrackRow.svelte";

  /** 与后端 `list_history(limit=50)` 对齐：满页即说明可能还有下一页。 */
  const PAGE_SIZE = 50;
  const STATUS_FILTERS = ["queued", "success", "skipped", "failed"] as const;

  let keyword = $state("");
  let stableKeyword = $state("");
  let status = $state("");
  let page = $state(0);

  let rows = $state<HistoryRow[]>([]);
  let loaded = $state(false);
  let error = $state("");
  let feedback = $state<{ id: number; tone: Tone; text: string } | null>(null);

  /** 只认最后一次发出的请求，慢响应不会覆盖新筛选的结果。 */
  let requestSeq = 0;

  const columns = $derived(trackColumns({ index: true, status: true, date: true }));
  const filtering = $derived(keyword.trim().length > 0 || status !== "");
  const statusOptions = $derived([
    { value: "", label: t("history.all") },
    ...STATUS_FILTERS.map((value) => ({ value, label: statusText(value) })),
  ]);
  /** 可播放行 = 有落盘路径的行；播放队列的索引必须在这份列表里算。 */
  const playable = $derived(rows.filter((row) => row.save_path !== null));
  const tracks = $derived(
    playable.map(
      (row): Track => ({
        id: String(row.id),
        title: row.title ?? "",
        artist: row.artist,
        streamUrl: `/api/history/${row.id}/stream`,
      }),
    ),
  );

  async function load(q: string, st: string, pg: number) {
    const seq = (requestSeq += 1);
    const parts: string[] = [];
    if (q) parts.push(`q=${encodeURIComponent(q)}`);
    if (st) parts.push(`status=${encodeURIComponent(st)}`);
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

  // 关键词去抖：停顿 250ms 后稳定下来的值才触发重取，同时回到第一页。
  $effect(() => {
    const next = keyword;
    const timer = setTimeout(() => {
      stableKeyword = next;
      page = 0;
    }, 250);
    return () => clearTimeout(timer);
  });

  // 筛选条件（去抖后的关键词、状态、页码）任一变化都重取。
  $effect(() => {
    const q = stableKeyword.trim();
    const st = status;
    const pg = page;
    void load(q, st, pg);
  });

  function setStatus(next: string) {
    if (next === status) return;
    status = next;
    page = 0;
  }

  function menuFor(row: HistoryRow): RowMenuItem[] {
    const items: RowMenuItem[] = [];
    if (row.save_path) {
      items.push({ label: t("history.copyPath"), onselect: () => void copyPath(row) });
    }
    items.push({ label: t("history.redownload"), onselect: () => void redownload(row) });
    return items;
  }

  function playFrom(row: HistoryRow) {
    const index = playable.findIndex((item) => item.id === row.id);
    if (index < 0) return;
    player.play(tracks, index);
  }

  async function copyPath(row: HistoryRow) {
    const path = row.save_path;
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      feedback = { id: row.id, tone: "done", text: t("history.copied") };
    } catch (err) {
      feedback = { id: row.id, tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  async function redownload(row: HistoryRow) {
    try {
      await api.post<{ items: DownloadItemResult[] }>("/api/downloads", {
        message_refs: [{ chat_id: row.chat_id, message_id: row.message_id }],
      });
      feedback = { id: row.id, tone: "done", text: t("history.redownloaded") };
    } catch (err) {
      feedback = { id: row.id, tone: "fail", text: errorText(err, t("common.error")) };
    }
  }
</script>

<PageHeader title={t("history.title")} lede={t("history.lede")}>
  {#snippet aside()}
    <span class="tabular text-caption text-muted-foreground">
      {t("history.found", { n: rows.length })}
    </span>
  {/snippet}
</PageHeader>

<div class="card flex flex-wrap items-center gap-3 p-4 md:p-5">
  <Input
    bind:value={keyword}
    type="search"
    class="w-full sm:w-72"
    placeholder={t("history.searchPlaceholder")}
    aria-label={t("history.searchPlaceholder")}
  />
  <Select
    type="single"
    value={status}
    items={statusOptions}
    onValueChange={(next) => setStatus(next)}
  >
    <SelectTrigger class="w-40" aria-label={t("history.filterStatus")}>
      <SelectValue placeholder={t("history.all")} />
    </SelectTrigger>
    <SelectContent>
      {#each statusOptions as option (option.value)}
        <SelectItem value={option.value}>{option.label}</SelectItem>
      {/each}
    </SelectContent>
  </Select>

  {#if page > 0 || rows.length >= PAGE_SIZE}
    <div class="ml-auto flex items-center gap-1">
      <button
        type="button"
        class="ui-transition grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        aria-label={t("history.pagePrev")}
        disabled={page === 0}
        onclick={() => (page -= 1)}
      >
        <ChevronLeftIcon class="size-4" aria-hidden="true" />
      </button>
      <span class="tabular text-caption text-muted-foreground">{page + 1}</span>
      <button
        type="button"
        class="ui-transition grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        aria-label={t("history.pageNext")}
        disabled={rows.length < PAGE_SIZE}
        onclick={() => (page += 1)}
      >
        <ChevronRightIcon class="size-4" aria-hidden="true" />
      </button>
    </div>
  {/if}
</div>

{#if error}
  <Note tone="fail">{error}</Note>
{/if}

{#if !loaded}
  <p class="text-caption text-muted-foreground">{t("common.loading")}</p>
{:else if rows.length === 0 && !error}
  <EmptyState title={filtering ? t("history.none") : t("history.empty")} />
{:else}
  <DataTable {columns}>
    {#each rows as row, index (row.id)}
      {@const path = row.save_path}
      {@const playing = player.current?.id === String(row.id)}
      {@const rowFeedback = feedback && feedback.id === row.id ? feedback : null}
      <TrackRow
        {columns}
        {index}
        title={row.title ?? t("common.unknown")}
        artist={row.artist}
        {path}
        missing={path ? null : t("history.noPath")}
        duration={row.duration_sec}
        size={row.file_size}
        date={row.created_at}
        status={{ tone: taskTone(row.status), label: statusText(row.status) }}
        playing={playing}
        playable={path !== null}
        playLabel={playing ? t("history.playing") : t("history.play")}
        onplay={() => playFrom(row)}
        menu={menuFor(row)}
      >
        {#snippet feedback()}
          {#if rowFeedback}
            <Note tone={rowFeedback.tone}>{rowFeedback.text}</Note>
          {/if}
        {/snippet}
      </TrackRow>
    {/each}
  </DataTable>
{/if}
