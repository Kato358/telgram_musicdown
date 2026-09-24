<script lang="ts" module>
  /** 统计卡的四个分类色（设计规范 §3.5）：只表达「这是哪一类事实」。
   *
   * 分类色不是状态色——状态永远只有 `--lamp-*` 一族（§8）；这里换成蓝色或琥珀色
   * 不表示「出错」，只表示「这是另一类数字」。 */
  export type StatTone = "primary" | "blue" | "violet" | "amber";

  export const STAT_TILE: Record<StatTone, string> = {
    primary: "bg-primary-soft text-primary",
    blue: "bg-accent-blue-soft text-accent-blue",
    violet: "bg-accent-violet-soft text-accent-violet",
    amber: "bg-accent-amber-soft text-accent-amber",
  };
</script>

<script lang="ts">
  /** 统计卡（设计规范 §5.6）：图标块 + 名称 + 数值 + 一行上下文，窄屏两列、宽屏四列。
   *
   * 数值只接受已格式化的字符串（大小、计数一律走 `lib/format.ts`），组件不认识字节。
   * 给了 `href` 才出现右下角的圆形箭头——那是真入口（跳到对应的页），不是装饰；
   * 没有对应页面的数字就不带箭头，免得画一个点不动的按钮。
   */
  import type { Component } from "svelte";
  import ArrowRightIcon from "@lucide/svelte/icons/arrow-right";
  import Link from "./Link.svelte";

  interface Props {
    label: string;
    /** 已格式化的数值。 */
    value: string;
    /** 数值下方的一行上下文（进行中 / 首曲目 / 共 1 个）。 */
    hint?: string;
    tone?: StatTone;
    icon: Component;
    /** 该数字对应的页面；给了才渲染右下角箭头。 */
    href?: string;
    class?: string;
  }

  let {
    label,
    value,
    hint,
    tone = "primary",
    icon: Icon,
    href,
    class: className = "",
  }: Props = $props();

  const ICON_BTN =
    "ui-transition grid size-7 shrink-0 place-items-center rounded-full bg-primary-soft text-primary hover:bg-primary/15";
</script>

<section class="card relative flex flex-col gap-3 p-4 {className}">
  <span
    class="grid size-10 shrink-0 place-items-center rounded-chip {STAT_TILE[tone]}"
    aria-hidden="true"
  >
    <Icon class="size-5" />
  </span>

  <div class="flex flex-col gap-0.5">
    <p class="text-caption text-muted-foreground">{label}</p>
    <!-- 右栏在场的主栏 ~590px 时每张卡约 140px，h1 的 24px 仍放得下「117 MB」；
         xl 以下（单栏窄档）用 h2。 -->
    <p class="tabular text-h2 font-bold lg:text-h1">{value}</p>
  </div>

  {#if hint || href}
    <div class="mt-auto flex items-center justify-between gap-2">
      <p class="min-w-0 truncate text-caption text-muted-foreground">{hint ?? ""}</p>
      {#if href}
        <Link {href} class={ICON_BTN} title={label}>
          <ArrowRightIcon class="size-4" aria-hidden="true" />
        </Link>
      {/if}
    </div>
  {/if}
</section>
