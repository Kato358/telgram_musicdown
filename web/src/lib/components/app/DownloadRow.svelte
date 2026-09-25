<script lang="ts" module>
  import { t } from "$lib/i18n/index.svelte";
  import type { Column } from "./DataTable.svelte";

  /** 下载页的列（设计规范 §5.4）：勾选 / 歌曲 / 歌手 / 专辑 / 时长 / 大小 / 码率 / 入库时间 / 操作。
   *
   * 没有「状态」「进度」两列：正在跑的那些项归上面的队列卡（那里才有实时读数与控制），
   * 这张表是**下载记录**——记录里，状态由第二行的「文件名 / 失败原因 / 灯 + 词」说清，
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
  export const COL_BITRATE = "hidden w-14 shrink-0 text-right md:block";
  export const COL_DATE = "hidden w-20 shrink-0 text-right lg:block";
  /** 操作列（v3.10 直排）：播放（常驻）+ 重试 / 取消 / 删除（不再藏在「⋯」里）。 */
  export const COL_DOWNLOAD_ACTIONS = "flex w-[132px] shrink-0 items-center justify-end gap-1";

  export function downloadColumns(): Column[] {
    return [
      { key: "check", label: "", class: COL_CHECK },
      { key: "title", label: t("table.song"), class: COL_TITLE },
      { key: "artist", label: t("table.artist"), class: COL_ARTIST },
      { key: "album", label: t("table.album"), class: COL_ALBUM },
      { key: "duration", label: t("table.duration"), class: COL_DURATION },
      { key: "size", label: t("table.size"), class: COL_SIZE },
      { key: "bitrate", label: t("downloads.bitrate"), class: COL_BITRATE },
      { key: "date", label: t("table.date"), class: COL_DATE },
      { key: "actions", label: "", class: COL_DOWNLOAD_ACTIONS },
    ];
  }
</script>

<script lang="ts">
  /** 下载记录行（设计规范 §5.4，v3.10 精修）：一行 = 一次下载。
   *
   * 第二行只放**文件名**——目录段是噪音，Windows 全路径把每行的视觉焦点都拽走；
   * 整条路径挪进悬浮提示（title）。次级动作（重试 / 取消 / 删除）由状态直排
   * （不再藏在「⋯」里），md 起悬浮行时才显形（Hover Action），触屏常驻。
   * 元数据缺省显示 `—`（浅灰占位），不再满屏「未知」。
   * 行首封面用 Telegram 内嵌缩略图（`/api/history/{id}/cover`），加载失败退回音符占位。
   */
  import type { Snippet } from "svelte";
  import CircleAlertIcon from "@lucide/svelte/icons/circle-alert";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import RotateCcwIcon from "@lucide/svelte/icons/rotate-ccw";
  import Trash2Icon from "@lucide/svelte/icons/trash-2";
  import XIcon from "@lucide/svelte/icons/x";
  import type { HistoryRow } from "$lib/api/types";
  import {
    formatBitrate,
    formatDate,
    formatDuration,
    formatSize,
    progressRatio,
    splitPath,
  } from "$lib/format";
  import type { TaskReadings } from "$lib/stores/queue.svelte";
  import { statusText, taskTone } from "$lib/tone";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import { ROW_CLASS } from "./DataTable.svelte";
  import Lamp from "./Lamp.svelte";
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
    oncancel: () => void;
    ondelete: () => void;
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
    oncancel,
    ondelete,
    feedback,
    class: className = "",
  }: Props = $props();

  const playable = $derived(row.save_path !== null);
  const fileName = $derived(row.save_path ? splitPath(row.save_path).file : null);
  /** 标题缺失时回退到落盘文件名（去扩展名），不让「未知」顶在歌名位上。 */
  const fileStem = $derived(fileName ? fileName.replace(/\.[^.]+$/, "") : null);
  const title = $derived(row.title?.trim() || fileStem || t("common.unknown"));
  /** 文件名行只给「有真标题」的行做补充（标题本身就是文件名兜底时不重复）。 */
  const showFileLine = $derived(Boolean(fileName && row.title?.trim()));
  const missing = $derived(
    row.save_path === null && row.status === "success" ? t("downloads.noPath") : null,
  );
  const retryable = $derived(row.task_id !== null && taskRetryable(row.status));
  /** 进行中的行显示取消，其余可删；两个动作由状态推出（不再叠「⋯」）。 */
  const cancellable = $derived(
    row.task_id !== null && ["queued", "downloading", "paused"].includes(row.status),
  );
  /** 进行中的行第二行带实时百分比：读数是 SSE 帧优先、DB 快照兜底（`queue.readingsFor`）。 */
  const ratio = $derived(
    readings === null ? null : progressRatio(readings.received, readings.total),
  );
  const percent = $derived(
    ratio === null ? null : t("tasks.percent", { percent: Math.round(ratio * 100) }),
  );

  /** 次级动作（重试 / 取消 / 删除）常驻可见：修复回归——原 `md:opacity-0` 把按钮藏到
   *  悬浮之后，鼠标不到按钮上方就看不见也点不到；现在所有端都常驻，hover 只给底色。 */
  const HOVER_ACTION = "ui-transition";

  /** 封面加载失败（404 / 网络断）→ 退回音符占位。 */
  let coverFailed = $state(false);
  const coverUrl = $derived(`/api/history/${row.id}/cover`);
</script>

<li class="{ROW_CLASS} group {playing ? 'bg-primary-soft' : 'hover:bg-rule'} {className}">
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
          class="grid size-10 shrink-0 place-items-center overflow-hidden rounded-chip bg-primary-soft text-primary"
          aria-hidden="true"
        >
          {#if coverFailed}
            <MusicIcon class="size-4" />
          {:else}
            <img
              src={coverUrl}
              alt=""
              class="size-10 object-cover"
              loading="lazy"
              onerror={() => (coverFailed = true)}
            />
          {/if}
        </span>
        <div class="flex min-w-0 flex-1 flex-col gap-0.5">
          <p class="truncate text-body font-medium">{title}</p>
          {#if missing}
            <p class="flex min-w-0 items-center gap-1.5 text-caption text-destructive-text">
              <CircleAlertIcon class="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
              <span class="truncate">{missing}</span>
            </p>
          {:else if showFileLine}
            <!-- 文件名 = 落盘物证的最小形态；悬浮行/文件名可见完整路径 -->
            <p class="truncate text-code text-faint-foreground" title={row.save_path ?? ""}>
              {fileName}
            </p>
          {:else if row.error}
            <p class="truncate text-caption text-destructive-text" title={row.error}>
              {row.error}
            </p>
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
      <span
        class="{column.class} text-caption {row.artist?.trim()
          ? 'text-muted-foreground'
          : 'text-faint-foreground'}"
      >
        {row.artist?.trim() || t("common.placeholder")}
      </span>
    {:else if column.key === "album"}
      <span
        class="{column.class} text-caption {row.album?.trim()
          ? 'text-muted-foreground'
          : 'text-faint-foreground'}"
      >
        {row.album?.trim() || t("common.placeholder")}
      </span>
    {:else if column.key === "duration"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatDuration(row.duration_sec)}
      </span>
    {:else if column.key === "size"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatSize(row.file_size)}
      </span>
    {:else if column.key === "bitrate"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatBitrate(row.bitrate)}
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

        {#if retryable}
          <button
            type="button"
            class="{HOVER_ACTION} ui-transition grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground"
            aria-label={t("tasks.retry")}
            title={t("tasks.retry")}
            onclick={onretry}
          >
            <RotateCcwIcon class="size-4" aria-hidden="true" />
          </button>
        {/if}

        {#if cancellable}
          <button
            type="button"
            class="{HOVER_ACTION} ui-transition grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground"
            aria-label={t("tasks.cancel")}
            title={t("tasks.cancel")}
            onclick={oncancel}
          >
            <XIcon class="size-4" aria-hidden="true" />
          </button>
        {/if}

        {#if !cancellable}
          <button
            type="button"
            class="{HOVER_ACTION} ui-transition grid size-8 shrink-0 place-items-center rounded-full text-destructive-text hover:bg-destructive/10"
            aria-label={t("downloads.delete")}
            title={t("downloads.delete")}
            onclick={ondelete}
          >
            <Trash2Icon class="size-4" aria-hidden="true" />
          </button>
        {/if}
      </div>
    {/if}
  {/each}
</li>
