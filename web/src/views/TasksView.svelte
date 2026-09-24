<script lang="ts">
  /** 下载任务（设计规范 §10）：队列、进度、失败项。
   *
   * 列表事实源是 `queue`（DB 快照，SSE 状态事件触发重取）；进度/速率取 SSE 帧，
   * 所以行上的进度条是活的，而行的存在与否由数据库决定。
   * 行规格与仪表盘的「最近下载任务」共用 `TaskRow`，两处不会各自长歪。
   */
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HourglassIcon from "@lucide/svelte/icons/hourglass";
  import LinkIcon from "@lucide/svelte/icons/link";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import TriangleAlertIcon from "@lucide/svelte/icons/triangle-alert";
  import { api, errorText } from "$lib/api/client";
  import type { Column } from "$lib/components/app/DataTable.svelte";
  import type { DownloadItemResult, TaskRow as Task } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { statusText } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
  } from "$lib/components/ui/dialog";
  import { Textarea } from "$lib/components/ui/textarea";
  import DataTable from "$lib/components/app/DataTable.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import SectionCard from "$lib/components/app/SectionCard.svelte";
  import StatCard from "$lib/components/app/StatCard.svelte";
  import TaskRow, { type TaskAction } from "$lib/components/app/TaskRow.svelte";

  let links = $state("");
  let busy = $state(false);
  let notice = $state("");
  let failures = $state<DownloadItemResult[]>([]);
  let error = $state("");
  let removeOpen = $state(false);
  let removeTarget = $state<Task | null>(null);
  let removing = $state(false);
  let removeError = $state("");

  const failedCount = $derived(queue.failedCount);
  const columns = $derived<Column[]>([
    { key: "status", label: t("table.status"), class: "hidden w-20 shrink-0 sm:block" },
    { key: "task", label: t("table.task"), class: "min-w-0 flex-1" },
    { key: "progress", label: t("table.progress"), class: "hidden w-40 shrink-0 lg:flex" },
    {
      key: "actions",
      label: t("table.actions"),
      class: "flex w-[184px] shrink-0 items-center justify-end gap-2",
    },
  ]);

  /** 队列四态：与仪表盘的统计卡同一套分类色，同义同色。 */
  const cards = $derived([
    {
      key: "queued",
      label: statusText("queued"),
      value: queue.queued.length,
      tone: "blue" as const,
      icon: HourglassIcon,
    },
    {
      key: "downloading",
      label: statusText("downloading"),
      value: queue.downloading.length,
      tone: "primary" as const,
      icon: DownloadIcon,
    },
    {
      key: "paused",
      label: statusText("paused"),
      value: queue.paused.length,
      tone: "violet" as const,
      icon: PauseIcon,
    },
    {
      key: "failed",
      label: statusText("failed"),
      value: queue.failedCount,
      tone: "amber" as const,
      icon: TriangleAlertIcon,
    },
  ]);

  async function act(id: number, action: TaskAction) {
    error = "";
    try {
      await api.post(`/api/downloads/${id}/${action}`);
      await queue.refresh();
    } catch (err) {
      error = errorText(err, t("common.error"));
    }
  }

  function taskTitle(task: Task): string {
    return task.title?.trim() || `#${task.id}`;
  }

  function openRemove(task: Task) {
    removeTarget = task;
    removeError = "";
    removeOpen = true;
  }

  function closeRemove() {
    if (removing) return;
    removeOpen = false;
    removeTarget = null;
  }

  async function confirmRemove() {
    const target = removeTarget;
    if (target === null || removing) return;
    removing = true;
    removeError = "";
    try {
      await api.delete<{ ok: boolean }>(`/api/downloads/${target.id}`);
      removeOpen = false;
      removeTarget = null;
      await queue.refresh();
    } catch (err) {
      removeError = errorText(err, t("common.error"));
    } finally {
      removing = false;
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

<div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
  {#each cards as card (card.key)}
    <StatCard label={card.label} value={String(card.value)} tone={card.tone} icon={card.icon} />
  {/each}
</div>

<SectionCard title={t("tasks.pasteLabel")} hint={t("tasks.pasteHint")} icon={LinkIcon}>
  <div class="flex flex-col gap-3">
    <Textarea
      id="task-links"
      bind:value={links}
      rows={2}
      class="text-code"
      placeholder={t("tasks.pastePlaceholder")}
      aria-label={t("tasks.pasteLabel")}
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
</SectionCard>

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
      <TaskRow
        {columns}
        {task}
        progress={queue.readings(task)}
        onact={(action) => void act(task.id, action)}
        ondelete={() => openRemove(task)}
      />
    {/each}
  </DataTable>
{/if}

<Dialog
  bind:open={removeOpen}
  onOpenChange={(open) => {
    if (!open && !removing) removeTarget = null;
  }}
>
  <DialogContent>
    {#if removeTarget}
      <DialogHeader>
        <DialogTitle class="text-h2 font-semibold">
          {t("tasks.deleteTitle")}
        </DialogTitle>
        <DialogDescription class="text-caption">
          {t("tasks.deleteBody", { title: taskTitle(removeTarget) })}
        </DialogDescription>
      </DialogHeader>

      {#if removeError}
        <Note tone="fail">{removeError}</Note>
      {/if}

      <DialogFooter>
        <Button variant="outline" disabled={removing} onclick={closeRemove}>
          {t("common.cancel")}
        </Button>
        <Button
          variant="destructive"
          size="lg"
          disabled={removing}
          onclick={() => void confirmRemove()}
        >
          {t("tasks.deleteConfirm")}
        </Button>
      </DialogFooter>
    {/if}
  </DialogContent>
</Dialog>
