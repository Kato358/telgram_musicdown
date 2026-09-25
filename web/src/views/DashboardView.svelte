<script lang="ts">
  /** 仪表盘（设计规范 §10）：一屏之内回答三件事——在传什么、库有多大、最近下了什么。
   *
   * 版面照参考图：欢迎横幅 + 四张统计卡 + 下载任务表（主栏）/ 快速操作 + 系统状态（右栏）。
   * 但「正在写入」那块淡绿状态卡留着，而且仍然紧跟统计卡——真实字节、速率、剩余与**落盘路径**
   * 是这个产品对用户的唯一承诺，它不能因为改版掉到看不见的地方。
   * 页头（H1）不重复：顶栏已经写着当前页名，横幅就是这一屏的开场。
   */
  import { onMount } from "svelte";
  import type { Component } from "svelte";
  import ActivityIcon from "@lucide/svelte/icons/activity";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HardDriveIcon from "@lucide/svelte/icons/hard-drive";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import RadioIcon from "@lucide/svelte/icons/radio";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SendIcon from "@lucide/svelte/icons/send";
  import TimerIcon from "@lucide/svelte/icons/timer";
  import ZapIcon from "@lucide/svelte/icons/zap";
  import { api, errorText } from "$lib/api/client";
  import { fetchTracks } from "$lib/api/library";
  import type { HistoryRow } from "$lib/api/types";
  import { formatCount, formatSize, formatUptime, splitPath } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { player } from "$lib/stores/player.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import { TONE_TEXT } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import ActionTileGrid from "$lib/components/app/ActionTileGrid.svelte";
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

  /** 仪表盘只放得下几行：先按「活着的排前面」排，同一档里新的在前。 */
  const STATUS_ORDER: Record<string, number> = {
    downloading: 0,
    paused: 1,
    queued: 2,
    failed: 3,
    success: 4,
    skipped: 5,
    cancelled: 6,
  };
  const ROWS = 5;

  let historyRows = $state<HistoryRow[]>([]);
  let sourceCount = $state(0);
  let error = $state("");

  /** 最近入库只显示前几行；推荐词用整页（50 条）里的歌手，选择面更宽。 */
  const recent = $derived(historyRows.slice(0, ROWS));
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

  /** 入口块：下载页合并了队列与曲库，入口少一块（2 列栅格排三块）。 */
  const quickItems = $derived([
    { label: t("dashboard.quickSearch"), icon: SearchIcon, href: pathOf("search") },
    { label: t("dashboard.quickAddSource"), icon: RadioTowerIcon, href: pathOf("sources") },
    { label: t("dashboard.quickDownloads"), icon: DownloadIcon, href: pathOf("downloads") },
  ]);

  async function load() {
    try {
      const [history, sourceRows] = await Promise.all([
        // 「最近入库」只说已入库的那批（status=success）：未下载完成的行 save_path
        // 还是 null，混进来会让它们顶着「文件不在磁盘」的红色文案——那是给
        // 已入库后文件丢失准备的提示，与「查看全部」指向的 ?status=success 同一判据。
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

  async function act(id: number, action: TaskAction) {
    error = "";
    try {
      await api.post(`/api/downloads/${id}/${action}`);
      await queue.refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  async function playFrom(row: HistoryRow) {
    error = "";
    try {
      // 队列上下文是整个曲库，不只是屏上的几行「最近」：点哪首，从哪首起播
      const tracks = await fetchTracks({ status: "success" });
      const index = tracks.findIndex((track) => track.id === String(row.id));
      if (index >= 0) player.play(tracks, index);
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

{#if sourceCount === 0 && queue.tasks.length === 0}
  <EmptyState title={t("dashboard.needSourcesTitle")} hint={t("dashboard.needSourcesHint")}>
    {#snippet actions()}
      <Button size="lg" onclick={() => navigate(pathOf("sources"))}>
        {t("dashboard.addSource")}
      </Button>
    {/snippet}
  </EmptyState>
{/if}

<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.45fr)] lg:gap-6">
  <div class="flex min-w-0 flex-col gap-4">
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

    <div class="grid grid-cols-2 gap-4 sm:grid-cols-4 sm:gap-5">
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
      <StatCard
        label={t("dashboard.statSources")}
        value={formatCount(stats.data?.sources.enabled ?? null)}
        hint={t("dashboard.statSourcesHint", { n: stats.data?.sources.total ?? 0 })}
        tone="amber"
        icon={RadioTowerIcon}
        href={pathOf("sources")}
      />
    </div>

    <DataTable {columns}>
      {#snippet header()}
        <h2 class="text-h2 font-semibold">{t("dashboard.tasks")}</h2>
        <span class="tabular text-caption text-muted-foreground">
          {t("dashboard.tasksCount", { n: queue.tasks.length })}
        </span>
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

    <SectionCard title={t("dashboard.recent")} icon={MusicIcon}>
      {#snippet actions()}
        <Link
          href={`${pathOf("downloads")}?status=success`}
          class="text-caption text-primary hover:text-primary-hover"
        >
          {t("dashboard.viewAll")} →
        </Link>
      {/snippet}

      {#if recent.length === 0}
        <p class="text-caption text-muted-foreground">{t("dashboard.recentEmpty")}</p>
      {:else}
        <ul class="flex flex-col gap-1">
          {#each recent as row (row.id)}
            {@const path = row.save_path}
            {@const stem = path ? splitPath(path).file.replace(/\.[^.]+$/, "") : null}
            {@const playing = player.current?.id === String(row.id)}
            <li class="flex items-center gap-3 rounded-nav px-1 py-1.5 hover:bg-rule">
              <span
                class="grid size-9 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
                aria-hidden="true"
              >
                <MusicIcon class="size-4" />
              </span>
              <div class="flex min-w-0 flex-1 flex-col">
                <p class="truncate text-body font-medium">
                  {row.title?.trim() || stem || t("common.placeholder")}
                </p>
                <p
                  class="truncate text-caption {path
                    ? 'text-muted-foreground'
                    : 'text-destructive-text'}"
                >
                  {#if path}{row.artist?.trim() || t("common.placeholder")}{:else}{t(
                      "downloads.noPath",
                    )}{/if}
                </p>
              </div>
              <button
                type="button"
                class="ui-transition grid size-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label={playing ? t("downloads.playing") : t("downloads.play")}
                aria-disabled={path === null}
                disabled={path === null}
                onclick={() => playFrom(row)}
              >
                {#if playing}
                  <PauseIcon class="size-4" aria-hidden="true" />
                {:else}
                  <PlayIcon class="size-4" aria-hidden="true" />
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </SectionCard>
  </div>

  <div class="flex min-w-0 flex-col gap-4">
    <SectionCard
      title={t("dashboard.quickTitle")}
      hint={t("dashboard.quickHint")}
      icon={ZapIcon}
      tone="brand"
    >
      <ActionTileGrid items={quickItems} />
    </SectionCard>

    <SectionCard title={t("dashboard.systemTitle")} icon={ActivityIcon}>
      {#snippet actions()}
        <Lamp
          tone={system.ok ? "done" : "wait"}
          label={system.ok ? t("dashboard.systemOk") : t("dashboard.systemAttention")}
        />
      {/snippet}

      <dl class="flex flex-col">
        {#each system.rows as row (row.key)}
          {@const Icon = row.icon}
          <div class="flex items-center gap-2 border-b border-rule py-2 last:border-b-0">
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
</div>
