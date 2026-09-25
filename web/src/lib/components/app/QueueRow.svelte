<script lang="ts" module>
  /** 队列行（设计规范 §5.4）：下载队列卡里的一行。
   *
   * v3.8 起只有 `full` 一档——右栏「下载进度」镜像卡已随单栏版式移除，
   * 不再需要为窄卡准备的第二套排版。
   */
</script>

<script lang="ts">
  /** 队列行（设计规范 §5.4）：正在跑的任务在队列卡里的一行。
   *
   * 一行 = DB 快照（状态、标题、元信息）+ SSE 进度帧（字节、速率、剩余），拼装收在
   * `queue.readings()`；动作由状态推出：下载中→暂停、已暂停→继续、进行中→取消
   * （队列里没有终态行）。动作一律是圆形图标键（参考图的画法），词由 `aria-label` 补全。
   */
  import ClockIcon from "@lucide/svelte/icons/clock";
  import Disc3Icon from "@lucide/svelte/icons/disc-3";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import UserIcon from "@lucide/svelte/icons/user";
  import XIcon from "@lucide/svelte/icons/x";
  import type { TaskRow } from "$lib/api/types";
  import { formatDuration, formatEta, formatRate, formatSize, progressRatio } from "$lib/format";
  import { taskTypeText, t } from "$lib/i18n/index.svelte";
  import type { TaskReadings } from "$lib/stores/queue.svelte";
  import { statusText } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import ProgressBar from "./ProgressBar.svelte";
  import type { TaskAction } from "./TaskRow.svelte";

  interface Props {
    task: TaskRow;
    progress: TaskReadings;
    onact: (action: TaskAction) => void;
    class?: string;
  }

  let { task, progress, onact, class: className = "" }: Props = $props();

  /** 标题行按参考图排成「歌手 / 歌名」；两边都缺时退回任务类型，不留一个光秃秃的 #id。 */
  const heading = $derived.by(() => {
    const artist = task.artist?.trim() || t("downloads.unknownArtist");
    const title = task.title?.trim() || t("common.unknown");
    return `${artist} / ${title}`;
  });
  const artistLine = $derived(task.artist?.trim() || taskTypeText(task.type));
  const ratio = $derived(progressRatio(progress.received, progress.total));
  /** 百分比只在真正开传（下载中/已暂停）后出现——等待中的 0% 是噪音。 */
  const showPercent = $derived(task.status === "downloading" || task.status === "paused");
  const percent = $derived(
    !showPercent || ratio === null
      ? null
      : t("tasks.percent", { percent: Math.round(ratio * 100) }),
  );
  /** 状态帽：词由 `statusText()` 说出，百分比跟在后面——「下载中 · 42%」。 */
  const statusLine = $derived(
    percent === null ? statusText(task.status) : `${statusText(task.status)} · ${percent}`,
  );
  const readings = $derived(
    [
      `${formatSize(progress.received)} / ${formatSize(progress.total)}`,
      progress.speed ? formatRate(progress.speed) : "",
      progress.eta ? `${t("tasks.eta")} ${formatEta(progress.eta)}` : "",
    ]
      .filter(Boolean)
      .join(" · "),
  );
</script>

<li class="flex items-center gap-3 rounded-nav bg-surface-subtle p-3 {className}">
  <span
    class="grid size-10 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
    aria-hidden="true"
  >
    <MusicIcon class="size-4" />
  </span>

  <div class="flex min-w-0 flex-1 flex-col gap-1">
    <p class="truncate text-body font-medium">{heading}</p>
    <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-caption text-muted-foreground">
      <span class="flex min-w-0 items-center gap-1">
        <UserIcon class="size-3.5 shrink-0" aria-hidden="true" />
        <span class="truncate">{artistLine}</span>
      </span>
      {#if task.album?.trim()}
        <span class="flex min-w-0 items-center gap-1">
          <Disc3Icon class="size-3.5 shrink-0" aria-hidden="true" />
          <span class="truncate">{task.album}</span>
        </span>
      {/if}
      {#if task.duration_sec !== null}
        <span class="tabular flex items-center gap-1">
          <ClockIcon class="size-3.5 shrink-0" aria-hidden="true" />
          {formatDuration(task.duration_sec)}
        </span>
      {/if}
      {#if task.error}
        <span class="truncate text-destructive-text" title={task.error}>{task.error}</span>
      {/if}
    </div>
  </div>

  <div class="flex w-28 shrink-0 flex-col gap-1 sm:w-40 lg:w-48">
    <span class="tabular truncate text-caption text-muted-foreground">{statusLine}</span>
    <ProgressBar {ratio} label={heading} />
    <p class="tabular truncate text-caption text-faint-foreground">{readings}</p>
  </div>
  <div class="flex shrink-0 items-center gap-1.5">
    {#if task.status === "downloading"}
      <Button
        variant="outline"
        size="icon"
        class="rounded-full"
        aria-label={t("tasks.pause")}
        title={t("tasks.pause")}
        onclick={() => onact("pause")}
      >
        <PauseIcon aria-hidden="true" />
      </Button>
    {:else if task.status === "paused"}
      <Button
        variant="outline"
        size="icon"
        class="rounded-full"
        aria-label={t("tasks.resume")}
        title={t("tasks.resume")}
        onclick={() => onact("resume")}
      >
        <PlayIcon aria-hidden="true" />
      </Button>
    {/if}
    <Button
      variant="outline"
      size="icon"
      class="rounded-full"
      aria-label={t("tasks.cancel")}
      title={t("tasks.cancel")}
      onclick={() => onact("cancel")}
    >
      <XIcon aria-hidden="true" />
    </Button>
  </div>
</li>
