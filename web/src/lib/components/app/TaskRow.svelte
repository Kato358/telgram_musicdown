<script lang="ts" module>
  /** 任务行的两种终态判据（仪表盘的任务行与下载页的行都据此推）。 */
  export function taskFinished(status: string): boolean {
    return ["success", "failed", "skipped", "cancelled"].includes(status);
  }

  export function taskRetryable(status: string): boolean {
    return ["failed", "cancelled", "skipped"].includes(status);
  }

  export type TaskAction = "pause" | "resume" | "retry" | "cancel";
</script>

<script lang="ts">
  /** 下载任务行（设计规范 §5.4、§5.6）：仪表盘「最近下载任务」用这一件。
   *
   * 一行 = DB 快照（行的存在、状态）+ SSE 进度帧（字节、速率、剩余），拼起来才是活的；
   * 两类数据的合并收在 `queue.readings()`，视图不各拼一遍。
   * 动作按钮由状态推出：下载中→暂停、已暂停→继续、可重试→重试、进行中→取消。
   */
  import type { TaskRow as Task } from "$lib/api/types";
  import MusicIcon from "@lucide/svelte/icons/music";
  import { formatEta, formatRate, formatSize, progressRatio } from "$lib/format";
  import { taskTypeText, t } from "$lib/i18n/index.svelte";
  import type { TaskReadings } from "$lib/stores/queue.svelte";
  import { statusText, taskTone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { ROW_CLASS, type Column } from "./DataTable.svelte";
  import Lamp from "./Lamp.svelte";
  import ProgressBar from "./ProgressBar.svelte";

  interface Props {
    columns: Column[];
    task: Task;
    progress: TaskReadings;
    onact: (action: TaskAction) => void;
    class?: string;
  }

  let { columns, task, progress, onact, class: className = "" }: Props = $props();

  /** 行内动作是软色药丸（参考图的做法）：底 `--primary-soft`、字 `--primary`，不描边；
   *  取消/删除仍走 destructive 的浅红底，破坏性动作不假装成普通按钮。 */
  const PILL = "rounded-full bg-primary-soft text-primary hover:bg-primary/15";

  const finished = $derived(taskFinished(task.status));
  const title = $derived(task.title?.trim() || `#${task.id}`);
  const artist = $derived(task.artist?.trim() || null);
  const ratio = $derived(progressRatio(progress.received, progress.total));
  const percent = $derived(
    ratio === null ? "--" : t("tasks.percent", { percent: Math.round(ratio * 100) }),
  );
</script>

<li class="{ROW_CLASS} hover:bg-rule {className}">
  {#each columns as column (column.key)}
    {#if column.key === "status"}
      <span class={column.class}>
        <Lamp tone={taskTone(task.status)} label={statusText(task.status)} />
      </span>
    {:else if column.key === "task"}
      <div class="flex {column.class} items-center gap-3">
        <span
          class="grid size-10 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
          aria-hidden="true"
        >
          <MusicIcon class="size-4" />
        </span>
        <div class="flex min-w-0 flex-1 flex-col gap-0.5">
          <p class="truncate text-body font-medium">{title}</p>
          <p class="truncate text-caption text-muted-foreground">
            {artist ?? taskTypeText(task.type)}
          </p>
          {#if task.error}
            <p class="truncate text-caption text-destructive-text" title={task.error}>
              {task.error}
            </p>
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
      </div>
    {:else if column.key === "progress"}
      <div class="{column.class} flex-col gap-1">
        {#if !finished}
          <ProgressBar {ratio} label={title} />
          <span class="tabular text-right text-caption text-muted-foreground">{percent}</span>
        {/if}
      </div>
    {:else if column.key === "actions"}
      <div class={column.class}>
        {#if task.status === "downloading"}
          <Button variant="ghost" size="xs" class={PILL} onclick={() => onact("pause")}>
            {t("tasks.pause")}
          </Button>
        {:else if task.status === "paused"}
          <Button variant="ghost" size="xs" class={PILL} onclick={() => onact("resume")}>
            {t("tasks.resume")}
          </Button>
        {/if}
        {#if taskRetryable(task.status)}
          <Button variant="ghost" size="xs" class={PILL} onclick={() => onact("retry")}>
            {t("tasks.retry")}
          </Button>
        {/if}
        {#if !finished}
          <Button
            variant="destructive"
            size="xs"
            class="rounded-full"
            onclick={() => onact("cancel")}
          >
            {t("tasks.cancel")}
          </Button>
        {/if}
      </div>
    {/if}
  {/each}
</li>
