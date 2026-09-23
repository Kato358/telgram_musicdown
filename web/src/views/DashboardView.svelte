<script lang="ts">
  /** 仪表盘：当前传输 + 队列事实 + 最近入库。
   *
   * 首屏的主角是「正在写入」那块：大到能一眼读出的梯级、速率、剩余时间，
   * 以及它下面的落盘路径 —— 这个产品对用户的唯一承诺就是文件落在哪儿。
   */
  import { onMount } from "svelte";
  import { api, errorText } from "$lib/api/client";
  import type { HistoryRow, SourceRow } from "$lib/api/types";
  import { formatDuration, formatEta, formatRate, formatSize, progressRatio, splitPath } from "$lib/format";
  import { statusText, taskTypeText, t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { taskTone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Ladder from "$lib/components/app/Ladder.svelte";
  import Link from "$lib/components/app/Link.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

  let recent = $state<HistoryRow[]>([]);
  let sources = $state<SourceRow[]>([]);
  let error = $state("");

  /** 主角任务：优先正在下载，其次暂停，最后排队。 */
  const lead = $derived(queue.downloading[0] ?? queue.paused[0] ?? queue.queued[0] ?? null);
  const live = $derived(lead ? events.progress[lead.id] : undefined);
  const received = $derived(live?.progress_bytes ?? lead?.progress_bytes ?? 0);
  const total = $derived(live?.total_bytes ?? lead?.total_bytes ?? null);
  const latestPath = $derived(recent.find((row) => row.save_path)?.save_path ?? null);

  const facts = $derived([
    { label: t("dashboard.sources"), value: `${sources.filter((s) => s.enabled).length}/${sources.length}` },
    { label: statusText("queued"), value: String(queue.queued.length) },
    { label: statusText("downloading"), value: String(queue.downloading.length) },
    { label: statusText("paused"), value: String(queue.paused.length) },
    { label: t("dashboard.failed"), value: String(queue.failedCount) },
  ]);

  onMount(async () => {
    try {
      const [history, sourceRows] = await Promise.all([
        api.get<HistoryRow[]>("/api/history?page=0"),
        api.get<SourceRow[]>("/api/sources"),
      ]);
      recent = history.slice(0, 6);
      sources = sourceRows;
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  });
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("dashboard.title")} lede={t("dashboard.lede")} />

  {#if error}
    <Note tone="fail">{error}</Note>
  {/if}

  {#if sources.length === 0}
    <EmptyState title={t("dashboard.needSourcesTitle")} hint={t("dashboard.needSourcesHint")}>
      {#snippet actions()}
        <Button onclick={() => navigate(pathOf("sources"))}>{t("dashboard.addSource")}</Button>
      {/snippet}
    </EmptyState>
  {/if}

  <section class="panel px-4 py-4">
    <div class="flex items-baseline justify-between gap-3">
      <h2 class="text-body font-medium">{t("dashboard.transferTitle")}</h2>
      {#if lead}
        <Lamp tone={taskTone(lead.status)} label={statusText(lead.status)} />
      {/if}
    </div>

    {#if lead}
      <p class="mt-1 truncate text-small text-muted-foreground">
        #{lead.id} | {taskTypeText(lead.type)}
      </p>
      <div class="mt-4">
        <Ladder
          segments={64}
          height={24}
          tone={taskTone(lead.status)}
          ratio={progressRatio(received, total)}
          label={t("dashboard.transferTitle")}
        />
      </div>
      <dl class="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <div class="flex items-baseline gap-1.5">
          <dt class="text-micro text-muted-foreground">{t("dashboard.received")}</dt>
          <dd class="tabular text-small">{formatSize(received)}</dd>
        </div>
        <div class="flex items-baseline gap-1.5">
          <dt class="text-micro text-muted-foreground">{t("dashboard.of")}</dt>
          <dd class="tabular text-small">{formatSize(total)}</dd>
        </div>
        <div class="flex items-baseline gap-1.5">
          <dt class="text-micro text-muted-foreground">{t("dashboard.speed")}</dt>
          <dd class="tabular text-small">{formatRate(live?.speed ?? null)}</dd>
        </div>
        <div class="flex items-baseline gap-1.5">
          <dt class="text-micro text-muted-foreground">{t("dashboard.remaining")}</dt>
          <dd class="tabular text-small">{formatEta(live?.eta ?? null)}</dd>
        </div>
      </dl>
    {:else}
      <p class="mt-1 text-small font-medium">{t("dashboard.idleTitle")}</p>
      <p class="mt-1 max-w-[56ch] text-small text-muted-foreground">{t("dashboard.idleHint")}</p>
      <div class="mt-4">
        <Button onclick={() => navigate(pathOf("search"))}>{t("dashboard.goSearch")}</Button>
      </div>
    {/if}

    <div class="mt-4 border-t border-rule pt-3">
      <p class="text-micro text-muted-foreground">{t("dashboard.destination")}</p>
      <p class="tabular mt-1 text-small break-all">{latestPath ?? "—"}</p>
    </div>
  </section>

  <div class="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
    <section class="min-w-0">
      <h2 class="text-body font-medium">{t("dashboard.library")}</h2>
      <dl class="mt-2 divide-y divide-rule border-t border-rule">
        {#each facts as fact (fact.label)}
          <div class="flex items-baseline justify-between gap-3 py-2">
            <dt class="text-small text-muted-foreground">{fact.label}</dt>
            <dd class="tabular text-small">{fact.value}</dd>
          </div>
        {/each}
      </dl>
    </section>

    <section class="min-w-0">
      <div class="flex items-baseline justify-between gap-3">
        <h2 class="text-body font-medium">{t("dashboard.recent")}</h2>
        <Link
          href={pathOf("history")}
          class="text-small text-primary underline-offset-4 hover:underline"
        >
          {t("dashboard.recentMore")}
        </Link>
      </div>

      {#if recent.length === 0}
        <p class="mt-2 text-small text-muted-foreground">{t("dashboard.recentEmpty")}</p>
      {:else}
        <ul class="mt-2 divide-y divide-rule border-t border-rule">
          {#each recent as row (row.id)}
            <li class="flex items-center gap-3 py-2.5">
              <div class="min-w-0 flex-1">
                <p class="truncate text-small font-medium">{row.title ?? t("common.unknown")}</p>
                {#if row.save_path}
                  {@const parts = splitPath(row.save_path)}
                  <p class="flex min-w-0 items-baseline gap-1 text-micro text-muted-foreground">
                    <span class="tabular truncate" title={row.save_path}>{parts.dir}</span>
                    <span class="tabular shrink-0" title={row.save_path}>{parts.file}</span>
                  </p>
                {:else}
                  <p class="truncate text-micro text-lamp-fail">{t("history.noPath")}</p>
                {/if}
              </div>
              <span class="tabular hidden w-14 shrink-0 text-right text-micro text-muted-foreground sm:block">
                {formatDuration(row.duration_sec)}
              </span>
              <span class="tabular hidden w-20 shrink-0 text-right text-micro text-muted-foreground md:block">
                {formatSize(row.file_size)}
              </span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>
</div>
