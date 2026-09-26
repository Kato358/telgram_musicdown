<script lang="ts">
  /** 仪表盘：一屏之内回答两件事——在传什么、库有多大。
   *
   * 单列版式：欢迎横幅 + 四张统计卡 + 「下载队列」卡 + 系统状态。
   * 队列卡只留「在飞」的任务行（下载中/等待/暂停/失败，带状态灯 + 进度 + 行内动作），
   * 终态任务不上榜；已入库的曲目不再单独展示，完整台账在下载页。
   * 页头（H1）不重复：顶栏已经写着当前页名，横幅就是这一屏的开场。
   */
  import { onMount } from "svelte";
  import type { Component } from "svelte";
  import ActivityIcon from "@lucide/svelte/icons/activity";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HardDriveIcon from "@lucide/svelte/icons/hard-drive";
  import MusicIcon from "@lucide/svelte/icons/music";
  import RadioIcon from "@lucide/svelte/icons/radio";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import SendIcon from "@lucide/svelte/icons/send";
  import TimerIcon from "@lucide/svelte/icons/timer";
  import { api, errorText } from "$lib/api/client";
  import type { HistoryRow } from "$lib/api/types";
  import { formatCount, formatSize, formatUptime } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import { TONE_TEXT } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import DataTable, { type Column } from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Link from "$lib/components/app/Link.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import SectionCard from "$lib/components/app/SectionCard.svelte";
  import StatCard from "$lib/components/app/StatCard.svelte";
  import HeroBanner from "$lib/components/app/HeroBanner.svelte";
  import TaskRow, { type TaskAction } from "$lib/components/app/TaskRow.svelte";

  /** 推荐搜索词：取自曲库里真实出现过的歌手——点一下就是搜这个人。
   *  没有可推荐的（空库）就不摆这一行，不为凑版面编词（§8 文案规则）。 */
  const SUGGESTION_LIMIT = 6;

  /** 任务段只收「在飞」的状态：下载中/暂停/等待排前、失败垫底（需要人管）。
   *  成功/跳过/取消是终态不上榜——成功的落进下面「最近入库」，其余是死胡同，
   *  完整台账在下载页；这样一首歌在卡里只出现一次，不与入库段重复。 */
  const STATUS_ORDER: Record<string, number> = {
    downloading: 0,
    paused: 1,
    queued: 2,
    failed: 3,
  };
  const ROWS = 5;

  let historyRows = $state<HistoryRow[]>([]);
  let sourceCount = $state(0);
  let error = $state("");

  /** 推荐词用整页（50 条）里的歌手，选择面更宽；status=success 即「真在架上的」那批。 */
  const suggestions = $derived(
    [
      ...new Set(
        historyRows.map((row) => row.artist?.trim()).filter((name): name is string => !!name),
      ),
    ]
      .slice(0, SUGGESTION_LIMIT)
      .map((name) => ({ label: name, query: name })),
  );

  const columns = $derived<Column[]>([
    { key: "status", label: t("table.status"), class: "hidden w-14 shrink-0 sm:block xl:w-20" },
    { key: "task", label: t("table.task"), class: "min-w-0 flex-1" },
    { key: "progress", label: t("table.progress"), class: "hidden w-20 shrink-0 lg:flex xl:w-28" },
    {
      key: "actions",
      label: t("table.actions"),
      class: "flex w-[120px] shrink-0 items-center justify-end gap-2 xl:w-[136px]",
    },
  ]);
  const rows = $derived(
    [...queue.tasks]
      .filter((task) => task.status in STATUS_ORDER)
      .sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || b.id - a.id)
      .slice(0, ROWS),
  );

  /** 系统状态：四条都是真读数，不做「看起来正常」的占位。 */
  interface SystemRow {
    key: string;
    label: string;
    value: string;
    icon: Component;
    /** 文字色类；不给就用主文本色。 */
    tone?: string;
  }

  const system = $derived.by((): { ok: boolean; rows: SystemRow[] } => {
    const data = stats.data;
    const ok = session.connected && events.connected && queue.failedCount === 0;
    return {
      ok,
      rows: [
        {
          key: "uptime",
          label: t("dashboard.systemUptime"),
          value: formatUptime(data?.uptime_sec ?? null),
          icon: TimerIcon,
        },
        {
          key: "engine",
          label: t("dashboard.systemEngine"),
          value:
            data && data.tasks.downloading > 0
              ? t("dashboard.systemEngineBusy", { n: data.tasks.downloading })
              : t("dashboard.systemEngineIdle"),
          icon: DownloadIcon,
        },
        {
          key: "stream",
          label: t("dashboard.systemStream"),
          value: events.connected ? t("dashboard.systemStreamOn") : t("dashboard.systemStreamOff"),
          icon: RadioIcon,
          tone: events.connected ? TONE_TEXT.done : TONE_TEXT.idle,
        },
        {
          key: "telegram",
          label: t("dashboard.systemTelegram"),
          value: session.connected
            ? t("dashboard.systemTelegramOn")
            : t("dashboard.systemTelegramOff"),
          icon: SendIcon,
          tone: session.connected ? TONE_TEXT.done : TONE_TEXT.idle,
        },
      ],
    };
  });

  async function load() {
    try {
      const [history, sourceRows] = await Promise.all([
        // 推荐词取自真实曲库：status=success 才是「真在架上的」那批
        api.get<HistoryRow[]>("/api/history?page=0&status=success"),
        api.get<{ id: number }[]>("/api/sources"),
      ]);
      historyRows = history;
      sourceCount = sourceRows.length;
      error = "";
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  /** 行内动作：取消/暂停/恢复/重试走 POST；「清除」删的是任务台账行（DELETE），
   *  失败的历史记录与已落盘文件都留着——仪表盘只借它把死任务从榜上拿下来。 */
  async function act(id: number, action: TaskAction) {
    error = "";
    try {
      if (action === "delete") {
        await api.delete(`/api/downloads/${id}`);
      } else {
        await api.post(`/api/downloads/${id}/${action}`);
      }
      await queue.refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  function search(keyword: string) {
    navigate(`${pathOf("search")}?q=${encodeURIComponent(keyword)}`);
  }

  onMount(() => {
    void load();
  });
</script>

{#if error}
  <Note tone="fail">{error}</Note>
{/if}

{#if !session.globalSearch && sourceCount === 0 && queue.tasks.length === 0}
  <EmptyState title={t("dashboard.needSourcesTitle")} hint={t("dashboard.needSourcesHint")}>
    {#snippet actions()}
      <Button size="lg" onclick={() => navigate(pathOf("sources"))}>
        {t("dashboard.addSource")}
      </Button>
    {/snippet}
  </EmptyState>
{/if}

<div class="flex min-w-0 flex-col gap-4 md:gap-6">
  <HeroBanner
    title={t("dashboard.welcomeTitle")}
    body={t("dashboard.welcomeBody")}
    search={{
      placeholder: t("dashboard.heroPlaceholder"),
      submitLabel: t("dashboard.heroSubmit"),
      tagsLabel: t("dashboard.recommended"),
      tags: suggestions,
      onsearch: search,
    }}
  />

  <div
    class="grid grid-cols-2 gap-4 {session.globalSearch
      ? 'sm:grid-cols-3'
      : 'sm:grid-cols-4'} sm:gap-5"
  >
    <StatCard
      label={t("dashboard.statTasks")}
      value={formatCount(queue.activeCount)}
      hint={t("dashboard.statTasksHint")}
      tone="primary"
      icon={DownloadIcon}
      href={pathOf("downloads")}
    />
    <StatCard
      label={t("dashboard.statLibrary")}
      value={formatCount(stats.data?.library.tracks ?? null)}
      hint={t("dashboard.statLibraryHint")}
      tone="blue"
      icon={MusicIcon}
      href={`${pathOf("downloads")}?status=success`}
    />
    <StatCard
      label={t("dashboard.statBytes")}
      value={formatSize(stats.data?.library.bytes ?? null)}
      hint={t("dashboard.statBytesHint")}
      tone="violet"
      icon={HardDriveIcon}
      href={`${pathOf("downloads")}?status=success`}
    />
    {#if !session.globalSearch}
      <StatCard
        label={t("dashboard.statSources")}
        value={formatCount(stats.data?.sources.enabled ?? null)}
        hint={t("dashboard.statSourcesHint", { n: stats.data?.sources.total ?? 0 })}
        tone="amber"
        icon={RadioTowerIcon}
        href={pathOf("sources")}
      />
    {/if}
  </div>

  <DataTable {columns}>
    {#snippet header()}
      <h2 class="text-h2 font-semibold">{t("dashboard.queue")}</h2>
      <Link
        href={pathOf("downloads")}
        class="ml-auto text-body text-primary hover:text-primary-hover"
      >
        {t("dashboard.viewAll")} →
      </Link>
    {/snippet}

    {#if rows.length === 0}
      <li class="px-4 py-8 text-body text-muted-foreground md:px-6">
        {t("dashboard.tasksEmpty")}
      </li>
    {:else}
      {#each rows as task (task.id)}
        <TaskRow
          {columns}
          {task}
          progress={queue.readings(task)}
          onact={(action) => void act(task.id, action)}
        />
      {/each}
    {/if}
  </DataTable>

  <SectionCard title={t("dashboard.systemTitle")} icon={ActivityIcon}>
    {#snippet actions()}
      <Lamp
        tone={system.ok ? "done" : "wait"}
        label={system.ok ? t("dashboard.systemOk") : t("dashboard.systemAttention")}
      />
    {/snippet}

    <dl class="grid gap-x-10 sm:grid-cols-2">
      {#each system.rows as row (row.key)}
        {@const Icon = row.icon}
        <div
          class="flex items-center gap-2 border-b border-rule py-2 last:border-b-0 sm:border-b-0 sm:py-3"
        >
          <Icon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <dt class="text-caption text-muted-foreground">{row.label}</dt>
          <dd class="tabular ml-auto text-caption {row.tone ?? 'text-foreground'}">
            {row.value}
          </dd>
        </div>
      {/each}
    </dl>
  </SectionCard>
</div>
