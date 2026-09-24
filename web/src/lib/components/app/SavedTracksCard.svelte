<script lang="ts">
  /** 右栏「已入库的曲目」（设计规范 §5.7）：最近落盘的几首。
   *
   * 它是主栏那张表的「最近几条」，所以只给播放与两个行内动作（复制路径 / 重新下载），
   * 编辑与筛选仍然在表里。存储占用在参考图里是独立的「存储空间」卡（`StorageCard`），
   * 不再挤在本卡的脚条里。
   */
  import type { Snippet } from "svelte";
  import EllipsisVerticalIcon from "@lucide/svelte/icons/ellipsis-vertical";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import type { HistoryRow } from "$lib/api/types";
  import { formatDate, formatDuration, formatSize } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { pathOf } from "$lib/router.svelte";
  import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
  } from "$lib/components/ui/dropdown-menu";
  import Link from "./Link.svelte";
  import SectionCard from "./SectionCard.svelte";

  interface Props {
    /** 最近入库的行（调用方决定取几条）。 */
    rows: HistoryRow[];
    /** 库里共几首（`stats.library.tracks`）。 */
    total: number | null;
    /** 正在播放的行 id（`player.current?.id`）。 */
    playingId: string | null;
    onplay: (row: HistoryRow) => void;
    oncopy: (row: HistoryRow) => void;
    onredownload: (row: HistoryRow) => void;
    feedback?: Snippet<[HistoryRow]>;
  }

  let { rows, total, playingId, onplay, oncopy, onredownload, feedback }: Props = $props();
</script>

<SectionCard
  title={t("downloads.savedTitle")}
  hint={t("downloads.savedHint", { n: total ?? 0 })}
  icon={MusicIcon}
  dense
>
  {#snippet actions()}
    <Link
      href={`${pathOf("downloads")}?status=success`}
      class="text-caption text-primary hover:text-primary-hover"
    >
      {t("dashboard.viewAll")} →
    </Link>
  {/snippet}

  {#if rows.length === 0}
    <p class="text-caption text-muted-foreground">{t("downloads.savedEmpty")}</p>
  {:else}
    <ul class="flex flex-col gap-1">
      {#each rows as row (row.id)}
        {@const playing = playingId === String(row.id)}
        <li class="flex items-center gap-2 rounded-nav py-1 hover:bg-rule">
          <span
            class="grid size-9 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
            aria-hidden="true"
          >
            <MusicIcon class="size-4" />
          </span>

          <div class="flex min-w-0 flex-1 flex-col gap-0.5">
            <p class="truncate text-body font-medium">{row.title ?? t("common.unknown")}</p>
            <!-- 没有的字段直接不出现（"--·--·" 是噪音），日期总在最后。 -->
            <p class="tabular truncate text-caption text-muted-foreground">
              {[
                row.duration_sec !== null ? formatDuration(row.duration_sec) : "",
                row.file_size !== null ? formatSize(row.file_size) : "",
                formatDate(row.created_at),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            {#if feedback}
              {@render feedback(row)}
            {/if}
          </div>

          <button
            type="button"
            class="ui-transition grid size-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={playing ? t("downloads.playing") : t("downloads.play")}
            aria-disabled={row.save_path === null}
            disabled={row.save_path === null}
            onclick={() => onplay(row)}
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
              {#if row.save_path}
                <DropdownMenuItem onSelect={() => oncopy(row)}>
                  {t("downloads.copyPath")}
                </DropdownMenuItem>
              {/if}
              <DropdownMenuItem onSelect={() => onredownload(row)}>
                {t("downloads.redownload")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </li>
      {/each}
    </ul>
  {/if}
</SectionCard>
