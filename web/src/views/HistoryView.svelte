<script lang="ts">
  /** 下载历史：已入库的音轨与它们的落盘位置。
   *
   * 落盘路径是这一页的主信息面，所以它是每行的第二行（等宽、可悬停看全）；
   * 列表事实源是后端分页结果（`/api/history`，DB 才是唯一事实源），
   * 关键词做 250ms 去抖后再重取，避免每个按键都打一次接口。
   */
  import { api, errorText } from "$lib/api/client";
  import type { DownloadItemResult, HistoryRow } from "$lib/api/types";
  import { formatDate, formatDuration, formatSize, splitPath } from "$lib/format";
  import { statusText, t } from "$lib/i18n/index.svelte";
  import { player, type Track } from "$lib/stores/player.svelte";
  import { taskTone, type Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
  } from "$lib/components/ui/select";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

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
  let redownloadingId = $state<number | null>(null);

  /** 只认最后一次发出的请求，慢响应不会覆盖新筛选的结果。 */
  let requestSeq = 0;
  let feedbackTimer: ReturnType<typeof setTimeout> | null = null;

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

  $effect(() => () => {
    if (feedbackTimer !== null) clearTimeout(feedbackTimer);
  });

  function setStatus(next: string) {
    if (next === status) return;
    status = next;
    page = 0;
  }

  function notify(id: number, tone: Tone, text: string, autoClear = false) {
    feedback = { id, tone, text };
    if (feedbackTimer !== null) {
      clearTimeout(feedbackTimer);
      feedbackTimer = null;
    }
    if (autoClear) {
      feedbackTimer = setTimeout(() => {
        feedback = null;
        feedbackTimer = null;
      }, 2000);
    }
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
      notify(row.id, "done", t("history.copied"), true);
    } catch (err) {
      notify(row.id, "fail", errorText(err, t("common.error")));
    }
  }

  async function redownload(row: HistoryRow) {
    redownloadingId = row.id;
    try {
      await api.post<{ items: DownloadItemResult[] }>("/api/downloads", {
        message_refs: [{ chat_id: row.chat_id, message_id: row.message_id }],
      });
      notify(row.id, "done", t("history.redownloaded"));
    } catch (err) {
      notify(row.id, "fail", errorText(err, t("common.error")));
    } finally {
      redownloadingId = null;
    }
  }
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("history.title")} lede={t("history.lede")}>
    {#snippet aside()}
      <span class="tabular text-micro text-muted-foreground">
        {t("history.found", { n: rows.length })}
      </span>
    {/snippet}
  </PageHeader>

  <div class="flex flex-wrap items-center gap-2">
    <Input
      bind:value={keyword}
      type="search"
      class="w-full tabular sm:w-72"
      placeholder={t("history.searchPlaceholder")}
      aria-label={t("history.searchPlaceholder")}
    />
    <span class="text-micro text-muted-foreground">{t("history.filterStatus")}</span>
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
        <Button variant="ghost" size="xs" disabled={page === 0} onclick={() => (page -= 1)}>
          {t("history.pagePrev")}
        </Button>
        <Button
          variant="ghost"
          size="xs"
          disabled={rows.length < PAGE_SIZE}
          onclick={() => (page += 1)}
        >
          {t("history.pageNext")}
        </Button>
      </div>
    {/if}
  </div>

  {#if error}
    <Note tone="fail">{error}</Note>
  {/if}

  {#if !loaded}
    <p class="text-micro text-muted-foreground">{t("common.loading")}</p>
  {:else if rows.length === 0 && !error}
    <EmptyState title={filtering ? t("history.none") : t("history.empty")} />
  {:else}
    <ul class="divide-y divide-rule">
      {#each rows as row (row.id)}
        {@const path = row.save_path}
        {@const playing = player.current?.id === String(row.id)}
        {@const rowFeedback = feedback && feedback.id === row.id ? feedback : null}
        <li class="flex flex-col gap-1.5 py-2.5">
          <div class="flex items-center gap-3">
            <span class="hidden w-20 shrink-0 sm:flex">
              {#if row.status !== "success"}
                <Lamp tone={taskTone(row.status)} label={statusText(row.status)} />
              {/if}
            </span>

            <div class="min-w-0 flex-1">
              <p class="truncate text-small font-medium">{row.title ?? t("common.unknown")}</p>
              {#if path}
                {@const parts = splitPath(path)}
                <p class="flex min-w-0 items-baseline gap-1 text-micro text-muted-foreground">
                  {#if row.artist}<span class="mr-1 shrink-0">{row.artist}</span>{/if}
                  <span class="tabular truncate" title={path}>{parts.dir}</span>
                  <span class="tabular shrink-0 text-foreground" title={path}>{parts.file}</span>
                </p>
              {:else}
                <p class="truncate text-micro text-muted-foreground">
                  {#if row.artist}<span class="mr-2">{row.artist}</span>{/if}
                  <span class="text-lamp-fail">{t("history.noPath")}</span>
                </p>
              {/if}
            </div>

            <span
              class="tabular hidden w-14 shrink-0 text-right text-micro text-muted-foreground sm:block"
              title={t("history.duration")}
            >
              {formatDuration(row.duration_sec)}
            </span>
            <span
              class="tabular hidden w-20 shrink-0 text-right text-micro text-muted-foreground md:block"
              title={t("history.size")}
            >
              {formatSize(row.file_size)}
            </span>
            <span
              class="tabular hidden w-24 shrink-0 text-right text-micro text-muted-foreground lg:block"
              title={t("history.date")}
            >
              {formatDate(row.created_at)}
            </span>

            <div class="flex shrink-0 items-center gap-1">
              <Button
                variant={playing ? "secondary" : "outline"}
                size="xs"
                disabled={!path}
                title={path ? undefined : t("history.noPath")}
                onclick={() => playFrom(row)}
              >
                {playing ? t("history.playing") : t("history.play")}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                disabled={!path}
                title={path ? undefined : t("history.noPath")}
                onclick={() => void copyPath(row)}
              >
                {t("history.copyPath")}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                disabled={redownloadingId === row.id}
                onclick={() => void redownload(row)}
              >
                {t("history.redownload")}
              </Button>
            </div>
          </div>

          {#if rowFeedback}
            <Note tone={rowFeedback.tone}>{rowFeedback.text}</Note>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>
