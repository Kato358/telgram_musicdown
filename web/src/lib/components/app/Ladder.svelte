<script lang="ts">
  /** 传输梯级：全站唯一的进度与位置读数装置（设计规范 §4）。
   *
   * 三处用途、同一形状语言：任务行进度、播放条位置（可拖动）、音量（可拖动）。
   * ratio 为 null 表示总量未知：点亮左端游标并缓慢脉动，而不是假装 0%。
   */
  import { TONE_FILL, type Tone } from "$lib/tone";

  interface Props {
    /** 0..1；null = 总量未知（不确定态）。 */
    ratio?: number | null;
    segments?: number;
    /** 梯级高度（px）。 */
    height?: number;
    label: string;
    tone?: Tone;
    /** 可拖动、可键盘调值（配合 onSeek）。 */
    interactive?: boolean;
    onSeek?: (ratio: number) => void;
    class?: string;
  }

  let {
    ratio = null,
    segments = 32,
    height = 6,
    label,
    tone = "live",
    interactive = false,
    onSeek,
    class: className = "",
  }: Props = $props();

  const cursorLength = $derived(Math.max(2, Math.round(segments * 0.15)));
  const filled = $derived(ratio === null ? 0 : Math.round(ratio * segments));

  function segmentClass(index: number): string {
    if (ratio === null) {
      return index < cursorLength ? `${TONE_FILL[tone]} ladder-pulse` : "bg-meter-off";
    }
    return index < filled ? TONE_FILL[tone] : "bg-meter-off";
  }
</script>

<div
  class="relative flex w-full items-center gap-[2px] {className}"
  style="height: {height}px"
  role={interactive ? undefined : "img"}
  aria-label={interactive ? undefined : label}
>
  {#each Array.from({ length: segments }) as _, i (i)}
    <span class="ladder-step h-full flex-1 rounded-[1px] {segmentClass(i)}"></span>
  {/each}
  {#if interactive}
    <input
      type="range"
      class="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      min="0"
      max="1"
      step="0.001"
      aria-label={label}
      value={ratio ?? 0}
      oninput={(event) => onSeek?.(Number(event.currentTarget.value))}
    />
  {/if}
</div>
