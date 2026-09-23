<script lang="ts">
  /** 线型进度条（设计规范 §5.5）：轨道 4px、已播放段 = `--progress-fill`、
   *  拖拽圆点 12px（hover 14px，2px `--card` 描边）。
   *
   * 比例未知（总量还没报出来）时左端 15% 游标呼吸，而不是假装 0%；
   * 它是真 `<input type="range">`（透明覆盖条 + 键盘可达 + `aria-label`），不是自绘 div。
   */
  interface Props {
    /** 0..1；null = 总量未知。 */
    ratio: number | null;
    label: string;
    /** true = 可拖拽（播放位置、音量）；false = 只读读数（任务行）。 */
    interactive?: boolean;
    /** `aria-valuetext`，如时间码。 */
    valueText?: string;
    onseek?: (ratio: number) => void;
    class?: string;
  }

  let {
    ratio,
    label,
    interactive = false,
    valueText,
    onseek,
    class: className = "",
  }: Props = $props();

  const percent = $derived(ratio === null ? 0 : Math.min(100, Math.max(0, ratio * 100)));
</script>

<div
  class="group relative flex h-4 w-full items-center has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring {className}"
>
  <div class="h-1 w-full overflow-hidden rounded-full bg-progress-track">
    {#if ratio === null}
      <div class="dot-pulse h-full w-[15%] rounded-full bg-progress-fill"></div>
    {:else}
      <div class="h-full rounded-full bg-progress-fill" style="width: {percent}%"></div>
    {/if}
  </div>

  {#if interactive}
    {#if ratio !== null}
      <span
        class="ui-transition pointer-events-none absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-card group-hover:size-3.5"
        style="left: {percent}%"
      ></span>
    {/if}
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
