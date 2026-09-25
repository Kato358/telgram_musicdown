<script lang="ts" module>
  import { t } from "$lib/i18n/index.svelte";
  import type { Column } from "./DataTable.svelte";

  /** 下载页的列（设计规范 §5.4）：勾选 / 歌曲 / 歌手 / 专辑 / 时长 / 大小 / 入库时间 / 操作。
   *
   * 没有「状态」「进度」两列：正在跑的那些项归上面的队列卡（那里才有实时读数与控制），
   * 这张表是**下载记录**——记录里，状态由第二行的「灯 + 词 / 路径 / 失败原因」说清，
   * 进度不是记录的一部分。
   */
  export const COL_CHECK = "flex w-4 shrink-0 items-center";
  export const COL_TITLE = "min-w-0 flex-1";
  export const COL_ARTIST = "hidden w-14 shrink-0 truncate md:block";
  /** 专辑列只在 lg 出现：md 及以下主栏装不下八个定宽列（歌手与专辑同义相邻，
   *  先退场的是它）；lg 起右栏已移除（v3.8 单栏），全宽放得下它。 */
  export const COL_ALBUM = "hidden w-14 shrink-0 truncate lg:block";
  export const COL_DURATION = "w-12 shrink-0 text-right";
  export const COL_SIZE = "hidden w-14 shrink-0 text-right sm:block";
  export const COL_DATE = "hidden w-20 shrink-0 text-right lg:block";
  /** 操作列：播放 + 「⋯」（重试 / 复制路径 / 重新下载）。 */
  export const COL_DOWNLOAD_ACTIONS = "flex w-[72px] shrink-0 items-center justify-end gap-2";

  export function downloadColumns(): Column[] {
    return [
      { key: "check", label: "", class: COL_CHECK },
      { key: "title", label: t("table.song"), class: COL_TITLE },
      { key: "artist", label: t("table.artist"), class: COL_ARTIST },
      { key: "album", label: t("table.album"), class: COL_ALBUM },
      { key: "duration", label: t("table.duration"), class: COL_DURATION },
      { key: "size", label: t("table.size"), class: COL_SIZE },
      { key: "date", label: t("table.date"), class: COL_DATE },
      { key: "actions", label: "", class: COL_DOWNLOAD_ACTIONS },
    ];
  }
</script>

<script lang="ts">
  /** 下载记录行（设计规范 §5.4）：一行 = 一次下载。
   *
   * 第二行按「最该被看见的那件事」排：落盘路径（产品承诺的物证）→ 失败原因 → 状态灯 + 词；
   * 再没有别的就不写第二行。动作在「⋯」里：重试（台账还在的失败/取消/跳过行）、复制路径、重新下载；
   * 勾选框把行交给批量动作（重新下载所选），它是记录列表里唯一的多选语义。
   */
  import type { Snippet } from "svelte";
  import EllipsisVerticalIcon from "@lucide/svelte/icons/ellipsis-vertical";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import type { HistoryRow } from "$lib/api/types";
  import { formatDate, formatDuration, formatSize, progressRatio } from "$lib/format";
  import type { TaskReadings } from "$lib/stores/queue.svelte";
  import { statusText, taskTone } from "$lib/tone";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
  } from "$lib/components/ui/dropdown-menu";
  import { ROW_CLASS } from "./DataTable.svelte";
  import Lamp from "./Lamp.svelte";
  import PathText from "./PathText.svelte";
  import { taskRetryable } from "./TaskRow.svelte";

  interface Props {
    /** 与表头同一份列定义（`downloadColumns()` 的结果）。 */
    columns: Column[];
    row: HistoryRow;
    /** 该行当前任务的实时读数（`queue.readingsFor(row.task_id)`）；没有在跑的任务时为 null。 */
    readings: TaskReadings | null;
    playing: boolean;
    playLabel: string;
    selected: boolean;
    onselected: (selected: boolean) => void;
    onplay: () => void;
    onretry: () => void;
    onredownload: () => void;
    oncopy: () => void;
    feedback?: Snippet;
    class?: string;
  }

  let {
    columns,
    row,
    readings,
    playing,
    playLabel,
    selected,
    onselected,
    onplay,
    onretry,
    onredownload,
    oncopy,
    feedback,
    class: className = "",
  }: Props = $props();

  const title = $derived(row.title?.trim() || t("common.unknown"));
  const playable = $derived(row.save_path !== null);
  const missing = $derived(
    row.save_path === null && row.status === "success" ? t("downloads.noPath") : null,
  );
  const retryable = $derived(row.task_id !== null && taskRetryable(row.status));
  /** 进行中的行第二行带实时百分比：读数是 SSE 帧优先、DB 快照兜底（`queue.readingsFor`）。 */
  const ratio = $derived(
    readings === null ? null : progressRatio(readings.received, readings.total),
  );
  const percent = $derived(
    ratio === null ? null : t("tasks.percent", { percent: Math.round(ratio * 100) }),
  );
</script>

<li class="{ROW_CLASS} {playing ? 'bg-primary-soft' : 'hover:bg-rule'} {className}">
  {#each columns as column (column.key)}
    {#if column.key === "check"}
      <span class={column.class}>
        <Checkbox
          checked={selected}
          onCheckedChange={(value) => onselected(value === true)}
          aria-label={t("downloads.selectRow", { title })}
        />
      </span>
    {:else if column.key === "title"}
      <div class="flex {column.class} items-center gap-3">
        <span
          class="grid size-10 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
          aria-hidden="true"
        >
          <MusicIcon class="size-4" />
        </span>
        <div class="flex min-w-0 flex-1 flex-col gap-0.5">
          <p class="truncate text-body font-medium">{title}</p>
          {#if missing}
            <PathText path={null} {missing} />
          {:else if row.save_path}
            <PathText path={row.save_path} />
          {:else if row.error}
            <p class="truncate text-caption text-destructive-text" title={row.error}>{row.error}</p>
          {:else}
            <span class="flex items-center gap-2">
              <Lamp tone={taskTone(row.status)} label={statusText(row.status)} />
              {#if percent}
                <span class="tabular text-caption text-muted-foreground">{percent}</span>
              {/if}
            </span>
          {/if}
          {#if feedback}
            <div class="mt-1">{@render feedback()}</div>
          {/if}
        </div>
      </div>
    {:else if column.key === "artist"}
      <span class="{column.class} text-caption text-muted-foreground">
        {row.artist?.trim() || t("common.unknown")}
      </span>
    {:else if column.key === "album"}
      <span class="{column.class} text-caption text-muted-foreground">
        {row.album?.trim() || t("common.unknown")}
      </span>
    {:else if column.key === "duration"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatDuration(row.duration_sec)}
      </span>
    {:else if column.key === "size"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatSize(row.file_size)}
      </span>
    {:else if column.key === "date"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatDate(row.created_at)}
      </span>
    {:else if column.key === "actions"}
      <div class={column.class}>
        <button
          type="button"
          class="ui-transition grid size-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={playLabel}
          aria-disabled={!playable}
          disabled={!playable}
          onclick={onplay}
        >
          {#if playing}
            <PauseIcon class="size-4" aria-hidden="true" />
          {:else}
            <PlayIcon class="size-4" aria-hidden="true" />
          {/if}
        </button>

        <DropdownMenu>
          <DropdownMenuTrigger>
            {#snippet child({ props })}
              <button
                {...props}
                type="button"
                class="ui-transition grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground"
                aria-label={t("table.actions")}
              >
                <EllipsisVerticalIcon class="size-4" aria-hidden="true" />
              </button>
            {/snippet}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {#if retryable}
              <DropdownMenuItem onSelect={onretry}>{t("tasks.retry")}</DropdownMenuItem>
            {/if}
            {#if row.save_path}
              <DropdownMenuItem onSelect={oncopy}>{t("downloads.copyPath")}</DropdownMenuItem>
            {/if}
            <DropdownMenuItem onSelect={onredownload}>{t("downloads.redownload")}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    {/if}
  {/each}
</li>
