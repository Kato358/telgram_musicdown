<script lang="ts">
  /** 仪表盘（设计规范 §10）：当前传输 + 队列事实 + 最近入库。
   *
   * 首屏主角是「正在写入」那块淡绿状态卡：真实字节 / 速率 / 剩余 + 落盘路径框——
   * 这个产品对用户的唯一承诺就是文件落在哪儿。
   */
  import { onMount } from "svelte";
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import UploadIcon from "@lucide/svelte/icons/upload";
  import { api, errorText } from "$lib/api/client";
  import type { HistoryRow, SourceRow } from "$lib/api/types";
  import { formatEta, formatRate, formatSize } from "$lib/format";
  import { taskTypeText, t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { player, type Track } from "$lib/stores/player.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { taskTone, statusText, type Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Link from "$lib/components/app/Link.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import StatusCard from "$lib/components/app/StatusCard.svelte";
  import TrackRow, { trackColumns, type RowMenuItem } from "$lib/components/app/TrackRow.svelte";

  /** 推荐搜索词：状态卡的快捷标签组（§5.3）。 */
  const SUGGESTIONS = ["data", "download"];

  let recent = $state<HistoryRow[]>([]);
  let sources = $state<SourceRow[]>([]);
  let error = $state("");
  let feedback = $state<{ id: number; tone: Tone; text: string } | null>(null);

  const columns = $derived(trackColumns());

  /** 主角任务：优先正在下载，其次暂停，最后排队。 */
  const lead = $derived(queue.downloading[0] ?? queue.paused[0] ?? queue.queued[0] ?? null);
  const live = $derived(lead ? events.progress[lead.id] : undefined);
  const received = $derived(live?.progress_bytes ?? lead?.progress_bytes ?? 0);
  const total = $derived(live?.total_bytes ?? lead?.total_bytes ?? null);
  const latestPath = $derived(recent.find((row) => row.save_path)?.save_path ?? null);
  const playable = $derived(recent.filter((row) => row.save_path !== null));
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

  /** 队列事实：等待 / 下载中 / 已暂停 / 失败 / 源。 */
  const facts = $derived([
    { label: statusText("queued"), value: queue.queued.length },
    { label: statusText("downloading"), value: queue.downloading.length },
    { label: statusText("paused"), value: queue.paused.length },
    { label: t("dashboard.failed"), value: queue.failedCount },
    { label: t("dashboard.sources"), value: sources.length },
  ]);

  async function load() {
    try {
      const [history, sourceRows] = await Promise.all([
        api.get<HistoryRow[]>("/api/history?page=0"),
        api.get<SourceRow[]>("/api/sources"),
      ]);
      recent = history.slice(0, 5);
      sources = sourceRows;
      error = "";
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  function playFrom(row: HistoryRow) {
    const index = playable.findIndex((item) => item.id === row.id);
    if (index < 0) return;
    player.play(tracks, index);
  }

  function menuFor(row: HistoryRow): RowMenuItem[] {
    const items: RowMenuItem[] = [];
    if (row.save_path) {
      items.push({ label: t("history.copyPath"), onselect: () => void copyPath(row) });
    }
    items.push({ label: t("history.redownload"), onselect: () => void redownload(row) });
    return items;
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
      await api.post("/api/downloads", {
        message_refs: [{ chat_id: row.chat_id, message_id: row.message_id }],
      });
      feedback = { id: row.id, tone: "done", text: t("history.redownloaded") };
    } catch (err) {
      feedback = { id: row.id, tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  onMount(() => {
    void load();
  });
</script>

<PageHeader title={t("dashboard.title")} lede={t("dashboard.lede")} />

{#if error}
  <Note tone="fail">{error}</Note>
{/if}

{#if sources.length === 0 && queue.tasks.length === 0}
  <EmptyState title={t("dashboard.needSourcesTitle")} hint={t("dashboard.needSourcesHint")}>
    {#snippet actions()}
      <Button size="lg" onclick={() => navigate(pathOf("sources"))}>
        {t("dashboard.addSource")}
      </Button>
    {/snippet}
  </EmptyState>
{/if}

<StatusCard
  title={lead ? t("dashboard.transferTitle") : t("dashboard.idleTitle")}
  hint={lead ? `#${lead.id} | ${taskTypeText(lead.type)}` : t("dashboard.idleHint")}
  path={latestPath}
>
  {#snippet icon()}
    {#if lead}
      <UploadIcon class="size-5" />
    {:else}
      <CircleCheckIcon class="size-5" />
    {/if}
  {/snippet}

  {#if lead}
    <Lamp tone={taskTone(lead.status)} label={statusText(lead.status)} class="self-start" />

    <dl class="flex flex-wrap items-baseline gap-x-6 gap-y-2">
      <div class="flex items-baseline gap-2">
        <dt class="text-caption text-muted-foreground">{t("dashboard.received")}</dt>
        <dd class="tabular text-body">{formatSize(received)}</dd>
      </div>
      <div class="flex items-baseline gap-2">
        <dt class="text-caption text-muted-foreground">{t("dashboard.of")}</dt>
        <dd class="tabular text-body">{formatSize(total)}</dd>
      </div>
      <div class="flex items-baseline gap-2">
        <dt class="text-caption text-muted-foreground">{t("dashboard.speed")}</dt>
        <dd class="tabular text-body">{formatRate(live?.speed ?? null)}</dd>
      </div>
      <div class="flex items-baseline gap-2">
        <dt class="text-caption text-muted-foreground">{t("dashboard.remaining")}</dt>
        <dd class="tabular text-body">{formatEta(live?.eta ?? null)}</dd>
      </div>
    </dl>
  {:else}
    <div class="flex flex-wrap items-center gap-2">
      <Button size="lg" onclick={() => navigate(pathOf("search"))}>
        {t("dashboard.goSearch")}
      </Button>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <span class="text-caption text-muted-foreground">{t("dashboard.recommended")}</span>
      {#each SUGGESTIONS as word (word)}
        <button
          type="button"
          class="ui-transition rounded-full bg-primary-soft px-2.5 py-1 text-caption text-primary hover:bg-primary/15"
          onclick={() => navigate(`${pathOf("search")}?q=${encodeURIComponent(word)}`)}
        >
          #{word}
        </button>
      {/each}
    </div>
  {/if}

  <dl class="flex flex-wrap items-baseline gap-x-6 gap-y-2">
    {#each facts as fact (fact.label)}
      <div class="flex items-baseline gap-2">
        <dt class="text-caption text-muted-foreground">{fact.label}</dt>
        <dd class="tabular text-body">{fact.value}</dd>
      </div>
    {/each}
  </dl>
</StatusCard>

<DataTable {columns}>
  {#snippet header()}
    <h2 class="text-h2 font-semibold">{t("dashboard.library")}</h2>
    <span class="tabular text-caption text-muted-foreground">
      {t("dashboard.libraryCount", { n: recent.length })}
    </span>
    <Link
      href={pathOf("history")}
      class="ml-auto text-body text-primary hover:text-primary-hover"
    >
      {t("dashboard.viewAll")} →
    </Link>
  {/snippet}

  {#if recent.length === 0}
    <li class="px-6 py-8 text-body text-muted-foreground">{t("dashboard.recentEmpty")}</li>
  {:else}
    {#each recent as row, index (row.id)}
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
  {/if}
</DataTable>
