<script lang="ts">
  /** 波形进度（设计规范 §5.5）：纵向柱代替线条，是这一屏的签名元件。
   *
   * 柱宽 2px、间距均分、柱高 4–20px；柱高由曲目 key 稳定派生（哈希 seeded），
   * 换皮肤或重渲染都不抖动。**不是真实频谱**——不引入 AnalyserNode，零音频分析开销。
   * 与 ProgressBar 共用同一份比例数据，且同样包一层真 `<input type="range">`。
   */
  interface Props {
    /** 曲目 key：柱高的唯一随机源。 */
    seed: string;
    /** 0..1；null = 总量未知（左端游标呼吸）。 */
    ratio: number | null;
    label: string;
    bars?: number;
    interactive?: boolean;
    valueText?: string;
    onseek?: (ratio: number) => void;
    class?: string;
  }

  let {
    seed,
    ratio,
    label,
    bars = 96,
    interactive = false,
    valueText,
    onseek,
    class: className = "",
  }: Props = $props();

  /** FNV-1a：同一 key 每次产出同一串高度。 */
  function hash(text: string): number {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  const heights = $derived(
    Array.from({ length: bars }, (_, i) => 4 + (hash(`${seed}#${i}`) % 17)),
  );

  function played(i: number): boolean {
    if (ratio === null) return i < Math.max(1, Math.round(bars * 0.15));
    return (i + 0.5) / bars <= ratio;
  }
</script>

<div
  class="relative flex h-5 w-full items-center justify-between gap-0.5 has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring {className}"
>
  {#each heights as height, i (i)}
    <span
      class="w-0.5 shrink-0 rounded-[1px] {played(i)
        ? 'bg-progress-fill'
        : 'bg-progress-track'} {ratio === null && played(i) ? 'dot-pulse' : ''}"
      style="height: {height}px"
    ></span>
  {/each}

  {#if interactive}
    <input
      type="range"
      min="0"
      max="1"
      step="0.001"
      value={ratio ?? 0}
      aria-label={label}
      aria-valuetext={valueText}
      class="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      oninput={(event) => onseek?.(Number(event.currentTarget.value))}
    />
  {/if}
</div>
