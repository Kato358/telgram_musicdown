<script lang="ts">
  /** 线型进度条（设计规范 §5.5，v3.9 悬浮加粗）：轨道 4px、hover 6px、
   *  已播放段 = `--progress-fill`。
   *
   * 比例未知（总量还没报出来）时左端 15% 游标呼吸，而不是假装 0%。
   * 只读读数用（任务行 / 下载行）——拖拽/定位由 APlayer 自带进度条承担，
   * 故这里不再有 interactive/onseek/valueText 那套不可达分支。
   */
  interface Props {
    /** 0..1；null = 总量未知。 */
    ratio: number | null;
    label: string;
    class?: string;
  }

  let { ratio, label, class: className = "" }: Props = $props();

  const percent = $derived(ratio === null ? 0 : Math.min(100, Math.max(0, ratio * 100)));
</script>

<div
  class="group relative flex h-4 w-full items-center {className}"
  role="progressbar"
  aria-label={label}
  aria-valuemin="0"
  aria-valuemax="100"
  aria-valuenow={ratio === null ? undefined : Math.round(percent)}
>
  <!-- 轨道高度在 hover 时 4→6px（外层 h-4 固定，加粗不挤动周围布局） -->
  <div
    class="h-1 w-full overflow-hidden rounded-full bg-progress-track transition-[height] duration-150 ease-out group-hover:h-1.5"
  >
    {#if ratio === null}
      <div class="dot-pulse h-full w-[15%] rounded-full bg-progress-fill"></div>
    {:else}
      <div class="h-full rounded-full bg-progress-fill" style="width: {percent}%"></div>
    {/if}
  </div>
</div>
