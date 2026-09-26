<script lang="ts" module>
  import { t } from "$lib/i18n/index.svelte";
  import type { Column } from "./DataTable.svelte";

  /** 曲目行共用列（设计规范 §5.4）：表头（DataTable）与行引用同一份类串，列宽天然对齐。
   *
   * COL_CHECK 只在多选（批量下载）时由调用方拼进列首，平时不占位。
   */
  export const COL_CHECK = "flex w-4 shrink-0 items-center";
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
</script>

<script lang="ts">
  /** 曲目行（设计规范 §5.4）：曲库、下载历史、搜索结果、最近入库共用这一套行规格。
   *
   * 第二行是落盘路径——它是产品承诺的物证，所以是一级文本而不是脚注（§3.4）；
   * 文件不在磁盘时整行换成原因文案，该行播放键随之 `aria-disabled` 且不可点击。
   */
  import type { Snippet } from "svelte";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import { formatDate, formatDuration, formatSize } from "$lib/format";
  import { coverUrl } from "$lib/cover";
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
    /** 在线源行：第二行前缀一枚「在线」徽章，和频道来源一眼分得开。 */
    online?: boolean;
    duration?: number | null;
    size?: number | null;
    date?: string | null;
    status?: { tone: Tone; label: string } | null;
    playing?: boolean;
    /** false = 不可播放：播放键 `aria-disabled` 且不可点击。 */
    playable?: boolean;
    playLabel: string;
    onplay?: () => void;
    /** 搜索结果的下载直排按钮（不再藏在「⋯」菜单里）；不给就不渲染。
     *  回调带按钮中心的 viewport 坐标：「飞进侧边栏」的动画从这里起跳。 */
    downloadLabel?: string;
    ondownload?: (origin: { x: number; y: number }) => void;
    /** 多选（批量下载）：列定义里拼进 `check` 列时，这两项驱动行首勾选框。 */
    selected?: boolean;
    onselected?: (selected: boolean) => void;
    /** 勾选框的无障碍名；不给就用标题。 */
    selectLabel?: string;
    /** 封面查询（title/artist）：给了就按全局封面链路 /api/cover 取（本地标签 > api）。 */
    cover?: { title?: string | null; artist?: string | null } | null;
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
    online = false,
    duration = null,
    size = null,
    date = null,
    status = null,
    playing = false,
    playable = true,
    playLabel,
    onplay,
    downloadLabel,
    ondownload,
    selected = false,
    onselected,
    selectLabel,
    cover = null,
    feedback,
    class: className = "",
  }: Props = $props();

  const secondLine = $derived(
    [artist, online && subtitle ? `\u25cf ${subtitle}` : subtitle].filter(Boolean).join(" | "),
  );

  /** 封面加载失败（404 / 网络断）→ 退回音符占位（与下载页同款）。 */
  let coverFailed = $state(false);
  const coverSrc = $derived(coverUrl(cover?.title, cover?.artist));
</script>

<li class="{ROW_CLASS} {playing ? 'bg-primary-soft' : 'hover:bg-rule'} {className}">
  {#each columns as column (column.key)}
    {#if column.key === "check"}
      <span class={column.class}>
        <Checkbox
          checked={selected}
          onCheckedChange={(value) => onselected?.(value === true)}
          aria-label={selectLabel ?? title}
        />
      </span>
    {:else if column.key === "index"}
      <span class="{column.class} tabular text-caption text-faint-foreground">{index ?? ""}</span>
    {:else if column.key === "title"}
      <div class="flex {column.class} items-center gap-3">
        <span
          class="grid size-10 shrink-0 place-items-center overflow-hidden rounded-chip bg-primary-soft text-primary"
          aria-hidden="true"
        >
          {#if coverSrc && !coverFailed}
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

        {#if downloadLabel && ondownload}
          <!-- 与播放键同规格的圆形实底（多选批量下载的行入口）：浅一档的 soft 底保住
              「播放 = 试听、下载 = 入队」的主次，悬浮时填成主色。 -->
          <button
            type="button"
            class="ui-transition grid size-8 shrink-0 place-items-center rounded-full bg-primary-soft text-primary hover:bg-primary hover:text-primary-foreground active:scale-[0.98]"
            aria-label={downloadLabel}
            title={downloadLabel}
            onclick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              ondownload?.({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
            }}
          >
            <DownloadIcon class="size-4" aria-hidden="true" />
          </button>
        {/if}
      </span>
    {/if}
  {/each}
</li>
