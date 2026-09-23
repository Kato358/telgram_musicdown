<script lang="ts">
  /** 下载任务：队列、进度、失败项。
   *
   * 列表事实源是 `queue`（DB 快照，SSE 状态事件触发重取）；进度/速率取 SSE 帧，
   * 所以行上的梯级是活的，而行的存在与否由数据库决定。
   */
  import { api, errorText } from "$lib/api/client";
  import type { DownloadItemResult, TaskRow } from "$lib/api/types";
  import { formatEta, formatRate, formatSize, progressRatio } from "$lib/format";
  import { statusText, taskTypeText, t } from "$lib/i18n/index.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { taskTone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Textarea } from "$lib/components/ui/textarea";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Field from "$lib/components/app/Field.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Ladder from "$lib/components/app/Ladder.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

  let links = $state("");
  let busy = $state(false);
  let notice = $state("");
  let failures = $state<DownloadItemResult[]>([]);
  let error = $state("");

  const failedCount = $derived(queue.failedCount);

  function liveProgress(task: TaskRow) {
    const frame = events.progress[task.id];
    return {
      received: frame?.progress_bytes ?? task.progress_bytes,
      total: frame?.total_bytes ?? task.total_bytes,
      speed: frame?.speed ?? null,
      eta: frame?.eta ?? null,
    };
  }

  async function act(id: number, action: "pause" | "resume" | "cancel") {
    error = "";
    try {
      await api.post(`/api/downloads/${id}/${action}`);
      await queue.refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
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
      notice = t("tasks.enqueueResult", { n: resp.items.length - rejected.length });
      if (rejected.length > 0) {
        notice = `${notice} · ${t("tasks.enqueueErrors", { n: rejected.length })}`;
      }
      links = "";
      await queue.refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    } finally {
      busy = false;
    }
  }

  async function retryFailed() {
    error = "";
    notice = "";
    failures = [];
    try {
      const resp = await api.post<{ retried: number }>("/api/downloads/retry-failed");
      notice = t("tasks.retryResult", { n: resp.retried });
      await queue.refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("tasks.title")} lede={t("tasks.lede")}>
    {#snippet aside()}
      {#if failedCount > 0}
        <Button variant="outline" size="xs" onclick={() => void retryFailed()}>
          {t("tasks.retryFailed")}
        </Button>
      {/if}
    {/snippet}
  </PageHeader>

  <Field label={t("tasks.pasteLabel")} for="task-links">
    <div class="flex flex-col gap-2">
      <Textarea
        id="task-links"
        bind:value={links}
        rows={2}
        class="tabular"
        placeholder={t("tasks.pastePlaceholder")}
      />
      <div class="flex items-center gap-2">
        <Button disabled={busy || links.trim().length === 0} onclick={() => void enqueue()}>
          {busy ? t("tasks.enqueuing") : t("tasks.enqueue")}
        </Button>
        {#if notice}
          <Note tone={failures.length > 0 ? "wait" : "done"}>{notice}</Note>
        {/if}
      </div>
    </div>
  </Field>

  {#if error}
    <Note tone="fail">{error}</Note>
  {/if}

  {#each failures as item (item.url ?? `${item.chat_id}-${item.message_id}`)}
    <Note tone="fail">{item.url}：{item.error}</Note>
  {/each}

  {#if queue.tasks.length === 0}
    <EmptyState title={t("tasks.empty")} />
  {:else}
    <ul class="divide-y divide-rule">
      {#each queue.tasks as task (task.id)}
        {@const progress = liveProgress(task)}
        {@const tone = taskTone(task.status)}
        <li class="flex flex-col gap-2 py-3">
          <div class="flex items-center gap-3">
            <Lamp {tone} />
            <div class="min-w-0 flex-1">
              <p class="truncate text-small">
                <span class="font-medium">{statusText(task.status)}</span>
                <span class="tabular ml-2 text-micro text-muted-foreground">
                  #{task.id} | {taskTypeText(task.type)}
                </span>
              </p>
              {#if task.error}
                <p class="truncate text-micro text-lamp-fail" title={task.error}>{task.error}</p>
              {:else if task.retry_count > 0}
                <p class="text-micro text-muted-foreground">
                  {t("tasks.retryCount", { n: task.retry_count })}
                </p>
              {/if}
            </div>

            <div class="flex shrink-0 items-center gap-1">
              {#if task.status === "downloading"}
                <Button variant="outline" size="xs" onclick={() => void act(task.id, "pause")}>
                  {t("tasks.pause")}
                </Button>
              {:else if task.status === "paused"}
                <Button variant="outline" size="xs" onclick={() => void act(task.id, "resume")}>
                  {t("tasks.resume")}
                </Button>
              {/if}
              {#if ["queued", "downloading", "paused"].includes(task.status)}
                <Button variant="destructive" size="xs" onclick={() => void act(task.id, "cancel")}>
                  {t("tasks.cancel")}
                </Button>
              {/if}
            </div>
          </div>

          {#if task.status === "downloading" || task.status === "paused"}
            <div class="flex items-center gap-3 pl-[22px]">
              <div class="w-40 shrink-0 sm:w-64 lg:w-96">
                <Ladder
                  segments={48}
                  height={8}
                  tone={tone}
                  ratio={progressRatio(progress.received, progress.total)}
                  label={`#${task.id}`}
                />
              </div>
              <span class="tabular w-40 shrink-0 text-right text-micro text-muted-foreground">
                {t("tasks.received", { size: formatSize(progress.received) })} ·
                {t("tasks.total", { size: formatSize(progress.total) })}
              </span>
              <span class="tabular hidden w-28 shrink-0 text-right text-micro text-muted-foreground sm:block">
                {t("tasks.speed")} {formatRate(progress.speed)}
              </span>
              <span class="tabular hidden w-24 shrink-0 text-right text-micro text-muted-foreground md:block">
                {t("tasks.eta")} {formatEta(progress.eta)}
              </span>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>
