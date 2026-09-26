<script lang="ts" module>
  import { t } from "$lib/i18n/index.svelte";
  import type { Column } from "./DataTable.svelte";

  /** 本地曲库的列：封面标题 / 歌手 / 专辑 / 时长 / 大小 / 码率 / 入库时间 / 操作。
   *
   * 没有「状态」「进度」两列：台账行只有「文件在不在」一件事，由标题第二行说清。
   * 表头排序在视图层的 headerCell 里做（Excel 式点击表头），行只管照列渲染。
   */
  export const COL_TITLE = "min-w-0 flex-1";
  export const COL_ARTIST = "hidden w-24 shrink-0 truncate md:block";
  export const COL_ALBUM = "hidden w-24 shrink-0 truncate lg:block";
  export const COL_DURATION = "w-12 shrink-0 text-right";
  export const COL_SIZE = "hidden w-14 shrink-0 text-right sm:block";
  export const COL_BITRATE = "hidden w-16 shrink-0 text-right md:block";
  export const COL_DATE = "hidden w-20 shrink-0 text-right lg:block";
  /** 操作列：播放 + 删除（常驻）+ 重新下载（文件已删且挂着下载记录时出现）。 */
  export const COL_LIBRARY_ACTIONS = "flex w-[108px] shrink-0 items-center justify-end gap-1";

  export function libraryColumns(): Column[] {
    return [
      { key: "title", label: t("table.song"), class: COL_TITLE },
      { key: "artist", label: t("table.artist"), class: COL_ARTIST },
      { key: "album", label: t("library.albumLabel"), class: COL_ALBUM },
      { key: "duration", label: t("table.duration"), class: COL_DURATION },
      { key: "size", label: t("table.size"), class: COL_SIZE },
      { key: "bitrate", label: t("downloads.bitrate"), class: COL_BITRATE },
      { key: "date", label: t("table.date"), class: COL_DATE },
      { key: "actions", label: "", class: COL_LIBRARY_ACTIONS },
    ];
  }
</script>

<script lang="ts">
  /** 本地曲库行（FR-LIB）：一行 = 一条扫描台账。
   *
   * 台账与磁盘对账：文件还在 → 可播放；文件没了 → 第二行说「文件已删除」，
   * 挂着下载记录的行给「重新下载」（按记录里的 chat/message 重新入队）。
   * 操作列的删除 = 移除这条曲库记录（磁盘文件不动，文件还在的话下次扫描会重新入库）。
   * 封面走曲库行自己的链路（内嵌封面 > api 兜底），加载失败退音符占位。
   */
  import CircleAlertIcon from "@lucide/svelte/icons/circle-alert";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import RotateCcwIcon from "@lucide/svelte/icons/rotate-ccw";
  import Trash2Icon from "@lucide/svelte/icons/trash-2";
  import type { LocalTrackRow } from "$lib/api/types";
  import {
    formatBitrate,
    formatDate,
    formatDuration,
    formatSize,
    splitPath,
  } from "$lib/format";
  import { ROW_CLASS } from "./DataTable.svelte";

  interface Props {
    columns: ReturnType<typeof libraryColumns>;
    row: LocalTrackRow;
    playing: boolean;
    playLabel: string;
    onplay: () => void;
    onredownload: () => void;
    onremove: () => void;
    feedback?: string | null;
    class?: string;
  }

  let {
    columns,
    row,
    playing,
    playLabel,
    onplay,
    onredownload,
    onremove,
    feedback,
    class: className = "",
  }: Props = $props();

  const playable = $derived(!row.missing);
  const fileName = $derived(splitPath(row.rel_path).file);
  /** 标题缺失时回退文件名（扫描时后端已做了同名回退，这里只是兜底）。 */
  const title = $derived(row.title?.trim() || fileName);
  const showFileLine = $derived(Boolean(row.title?.trim()) && fileName !== row.title?.trim());
  const redownloadable = $derived(row.missing && row.chat_id !== null && row.message_id !== null);

  const ACTION_BASE =
    "ui-transition grid size-8 shrink-0 place-items-center rounded-full active:scale-[0.98]";
  const PLAY_ACTION = `${ACTION_BASE} bg-primary text-primary-foreground hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50`;
  const SOFT_ACTION = `${ACTION_BASE} bg-primary-soft text-primary hover:bg-primary/20`;
  const DELETE_ACTION = `${ACTION_BASE} bg-destructive/10 text-destructive-text hover:bg-destructive/20`;

  /** 封面加载失败（404 / 网络断）→ 退回音符占位。 */
  let coverFailed = $state(false);
  const coverSrc = $derived(`/api/local-library/${row.id}/cover`);
</script>

<li class="{ROW_CLASS} group {playing ? 'bg-primary-soft' : 'hover:bg-rule'} {className}">
  {#each columns as column (column.key)}
    {#if column.key === "title"}
      <div class="flex {column.class} items-center gap-3">
        <span
          class="grid size-10 shrink-0 place-items-center overflow-hidden rounded-chip bg-primary-soft text-primary"
          aria-hidden="true"
        >
          {#if !coverFailed}
            <img
              src={coverSrc}
              alt=""
              class="size-10 object-cover"
              loading="lazy"
              onerror={() => (coverFailed = true)}
            />
          {:else}
            <MusicIcon class="size-4" />
          {/if}
        </span>
        <div class="flex min-w-0 flex-1 flex-col gap-0.5">
          <p class="truncate text-body font-medium">{title}</p>
          {#if row.missing}
            <p class="flex min-w-0 items-center gap-1.5 text-caption text-muted-foreground">
              <CircleAlertIcon class="size-3.5 shrink-0" aria-hidden="true" />
              <span class="truncate">{t("library.fileMissing")}</span>
            </p>
          {:else if showFileLine}
            <!-- 文件名 = 落盘物证；悬浮可见相对路径 -->
            <p class="truncate text-code text-faint-foreground" title={row.rel_path}>
              {fileName}
            </p>
          {/if}
          {#if feedback}
            <div class="mt-1">
              <p class="text-caption text-primary">{feedback}</p>
            </div>
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
        title={row.album ?? undefined}
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
        {formatDate(row.first_seen_at)}
      </span>
    {:else if column.key === "actions"}
      <div class={column.class}>
        <button
          type="button"
          class={PLAY_ACTION}
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

        {#if redownloadable}
          <button
            type="button"
            class={SOFT_ACTION}
            aria-label={t("library.redownload")}
            title={t("library.redownload")}
            onclick={onredownload}
          >
            <RotateCcwIcon class="size-4" aria-hidden="true" />
          </button>
        {/if}

        <button
          type="button"
          class={DELETE_ACTION}
          aria-label={t("library.removeRecord")}
          title={t("library.removeRecord")}
          onclick={onremove}
        >
          <Trash2Icon class="size-4" aria-hidden="true" />
        </button>
      </div>
    {/if}
  {/each}
</li>
