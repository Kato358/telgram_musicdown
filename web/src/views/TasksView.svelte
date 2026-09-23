<script lang="ts">
  /** 下载任务（设计规范 §10）：队列、进度、失败项。
   *
   * 列表事实源是 `queue`（DB 快照，SSE 状态事件触发重取）；进度/速率取 SSE 帧，
   * 所以行上的进度条是活的，而行的存在与否由数据库决定。
   */
  import { api, errorText } from "$lib/api/client";
  import type { Column } from "$lib/components/app/DataTable.svelte";
  import type { DownloadItemResult, TaskRow } from "$lib/api/types";
  import { formatEta, formatRate, formatSize, progressRatio } from "$lib/format";
  import { taskTypeText, t } from "$lib/i18n/index.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { statusText, taskTone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Textarea } from "$lib/components/ui/textarea";
  import DataTable, { ROW_CLASS } from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Field from "$lib/components/app/Field.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import ProgressBar from "$lib/components/app/ProgressBar.svelte";

  let links = $state("");
  let busy = $state(false);
  let notice = $state("");
  let failures = $state<DownloadItemResult[]>([]);
  let error = $state("");

  const failedCount = $derived(queue.failedCount);
  const columns = $derived<Column[]>([
    { key: "status", label: t("table.status"), class: "hidden w-20 shrink-0 sm:block" },
    { key: "task", label: t("table.task"), class: "min-w-0 flex-1" },
    { key: "progress", label: t("table.progress"), class: "hidden w-40 shrink-0 lg:block" },
    { key: "actions", label: "", class: "flex w-[136px] shrink-0 items-center justify-end gap-2" },
  ]);

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

<PageHeader title={t("tasks.title")} lede={t("tasks.lede")}>
  {#snippet aside()}
    {#if failedCount > 0}
      <Button variant="outline" onclick={() => void retryFailed()}>
        {t("tasks.retryFailed")}
      </Button>
    {/if}
  {/snippet}
</PageHeader>

<div class="card p-4 md:p-5">
  <Field label={t("tasks.pasteLabel")} for="task-links">
    <div class="flex flex-col gap-3">
      <Textarea
        id="task-links"
        bind:value={links}
        rows={2}
        class="text-code"
        placeholder={t("tasks.pastePlaceholder")}
      />
      <div class="flex flex-wrap items-center gap-3">
        <Button size="lg" disabled={busy || links.trim().length === 0} onclick={() => void enqueue()}>
          {busy ? t("tasks.enqueuing") : t("tasks.enqueue")}
        </Button>
        {#if notice}
          <Note tone={failures.length > 0 ? "wait" : "done"}>{notice}</Note>
        {/if}
      </div>
    </div>
  </Field>
</div>

{#if error}
  <Note tone="fail">{error}</Note>
{/if}

{#each failures as item (item.url ?? `${item.chat_id}-${item.message_id}`)}
  <Note tone="fail">{item.url}：{item.error}</Note>
{/each}

{#if queue.tasks.length === 0}
  <EmptyState title={t("tasks.empty")} />
{:else}
  <DataTable {columns}>
    {#each queue.tasks as task (task.id)}
      {@const progress = liveProgress(task)}
      {@const tone = taskTone(task.status)}
      {@const finished = ["success", "failed", "skipped", "cancelled"].includes(task.status)}
      <li class="{ROW_CLASS} hover:bg-rule">
        <span class="hidden w-20 shrink-0 sm:block">
          <Lamp {tone} label={statusText(task.status)} />
        </span>

        <div class="flex min-w-0 flex-1 flex-col gap-0.5">
          <p class="truncate text-body font-medium">
            #{task.id} | {taskTypeText(task.type)}
          </p>
          {#if task.error}
            <p class="truncate text-caption text-destructive-text" title={task.error}>{task.error}</p>
          {:else if !finished}
            <p class="tabular truncate text-caption text-muted-foreground">
              {t("tasks.received", { size: formatSize(progress.received) })} ·
              {t("tasks.total", { size: formatSize(progress.total) })}
              {#if progress.speed}· {t("tasks.speed")} {formatRate(progress.speed)}{/if}
              {#if progress.eta}· {t("tasks.eta")} {formatEta(progress.eta)}{/if}
            </p>
          {:else if task.retry_count > 0}
            <p class="text-caption text-muted-foreground">
              {t("tasks.retryCount", { n: task.retry_count })}
            </p>
          {/if}
        </div>

        <div class="hidden w-40 shrink-0 lg:block">
          {#if !finished}
            <ProgressBar
              ratio={progressRatio(progress.received, progress.total)}
              label={`#${task.id}`}
            />
          {/if}
        </div>

        <div class="flex w-[136px] shrink-0 items-center justify-end gap-2">
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
      </li>
    {/each}
  </DataTable>
{/if}
