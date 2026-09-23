<script lang="ts" module>
  import { t } from "$lib/i18n/index.svelte";
  import type { Column } from "./DataTable.svelte";

  /** 曲目行共用列（设计规范 §5.4）：表头（DataTable）与行引用同一份类串，列宽天然对齐。 */
  export const COL_INDEX = "hidden w-8 shrink-0 text-right sm:block";
  export const COL_TITLE = "min-w-0 flex-1";
  export const COL_STATUS = "hidden w-20 shrink-0 sm:block";
  export const COL_DURATION = "w-14 shrink-0 text-right";
  export const COL_SIZE = "hidden w-20 shrink-0 text-right md:block";
  export const COL_DATE = "hidden w-24 shrink-0 text-right lg:block";
  export const COL_ACTIONS = "flex w-[72px] shrink-0 items-center justify-end gap-2";

  /** 曲库 / 历史 / 搜索结果共用的一套列；`<768px` 只留「标题 + 时长」（§6.3）。 */
  export function trackColumns(
    options: { index?: boolean; status?: boolean; date?: boolean } = {},
  ): Column[] {
    const columns: Column[] = [];
    if (options.index) columns.push({ key: "index", label: t("table.index"), class: COL_INDEX });
    columns.push({ key: "title", label: t("table.title"), class: COL_TITLE });
    if (options.status) columns.push({ key: "status", label: t("table.status"), class: COL_STATUS });
    columns.push({ key: "duration", label: t("table.duration"), class: COL_DURATION });
    columns.push({ key: "size", label: t("table.size"), class: COL_SIZE });
    if (options.date) columns.push({ key: "date", label: t("table.date"), class: COL_DATE });
    columns.push({ key: "actions", label: "", class: COL_ACTIONS });
    return columns;
  }

  export interface RowMenuItem {
    label: string;
    onselect: () => void;
    /** 危险动作（移除记录等）用 --destructive-text，不用红底。 */
    danger?: boolean;
  }
</script>

<script lang="ts">
  /** 曲目行（设计规范 §5.4）：曲库、下载历史、搜索结果、最近入库共用这一套行规格。
   *
   * 第二行是落盘路径——它是产品承诺的物证，所以是一级文本而不是脚注（§3.4）；
   * 文件不在磁盘时整行换成原因文案，该行播放键随之 `aria-disabled` 且不可点击。
   */
  import type { Snippet } from "svelte";
  import EllipsisVerticalIcon from "@lucide/svelte/icons/ellipsis-vertical";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
  } from "$lib/components/ui/dropdown-menu";
  import { formatDate, formatDuration, formatSize } from "$lib/format";
  import type { Tone } from "$lib/tone";
  import { ROW_CLASS } from "./DataTable.svelte";
  import Lamp from "./Lamp.svelte";
  import PathText from "./PathText.svelte";

  interface Props {
    /** 与表头同一份列定义（`trackColumns()` 的结果）。 */
    columns: Column[];
    index?: number | null;
    title: string;
    artist?: string | null;
    /** 落盘路径；给了就渲染成第二行的路径文本。 */
    path?: string | null;
    /** 文件不在磁盘的原因文案；与 path 互斥，优先于 path。 */
    missing?: string | null;
    /** 无路径行的第二行（搜索结果的频道名等）。 */
    subtitle?: string | null;
    duration?: number | null;
    size?: number | null;
    date?: string | null;
    status?: { tone: Tone; label: string } | null;
    playing?: boolean;
    /** false = 不可播放：播放键 `aria-disabled` 且不可点击。 */
    playable?: boolean;
    playLabel: string;
    onplay?: () => void;
    menu?: RowMenuItem[];
    feedback?: Snippet;
    class?: string;
  }

  let {
    columns,
    index = null,
    title,
    artist = null,
    path = null,
    missing = null,
    subtitle = null,
    duration = null,
    size = null,
    date = null,
    status = null,
    playing = false,
    playable = true,
    playLabel,
    onplay,
    menu,
    feedback,
    class: className = "",
  }: Props = $props();

  const secondLine = $derived([artist, subtitle].filter(Boolean).join(" | "));
</script>

<li class="{ROW_CLASS} {playing ? 'bg-primary-soft' : 'hover:bg-rule'} {className}">
  {#each columns as column (column.key)}
    {#if column.key === "index"}
      <span class="{column.class} tabular text-caption text-faint-foreground">{index ?? ""}</span>
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
          {:else if path}
            <PathText {path} />
          {:else if secondLine}
            <p class="truncate text-caption text-muted-foreground">{secondLine}</p>
          {/if}
          {#if feedback}
            <div class="mt-1">{@render feedback()}</div>
          {/if}
        </div>
      </div>
    {:else if column.key === "status"}
      <span class={column.class}>
        {#if status}<Lamp tone={status.tone} label={status.label} />{/if}
      </span>
    {:else if column.key === "duration"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatDuration(duration)}
      </span>
    {:else if column.key === "size"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatSize(size)}
      </span>
    {:else if column.key === "date"}
      <span class="{column.class} tabular text-caption text-faint-foreground">
        {formatDate(date)}
      </span>
    {:else if column.key === "actions"}
      <span class={column.class}>
        <button
          type="button"
          class="ui-transition grid size-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={playLabel}
          aria-disabled={!playable}
          disabled={!playable}
          onclick={() => onplay?.()}
        >
          {#if playing}
            <PauseIcon class="size-4" aria-hidden="true" />
          {:else}
            <PlayIcon class="size-4" aria-hidden="true" />
          {/if}
        </button>

        {#if menu && menu.length > 0}
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
              {#each menu as item (item.label)}
                <DropdownMenuItem
                  onSelect={item.onselect}
                  class={item.danger ? "text-destructive-text" : ""}
                >
                  {item.label}
                </DropdownMenuItem>
              {/each}
            </DropdownMenuContent>
          </DropdownMenu>
        {/if}
      </span>
    {/if}
  {/each}
</li>
