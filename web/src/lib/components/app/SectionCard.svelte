<script lang="ts" module>
  /** 卡头两种皮肤（设计规范 §5.7）。
   *
   * - `plain`：白卡头 + 1px 下边，正文卡与表单卡用；
   * - `brand`：主色渐变卡头 + 白字，只用在一屏之内的入口卡（快速操作）上。
   */
  export type SectionTone = "plain" | "brand";
</script>

<script lang="ts">
  /** 分区卡（设计规范 §5.7）：卡头（可选图标 + 标题 + 说明 + 右侧动作）+ 卡体。
   *
   * 它是「一屏内的一个分区」的统一外壳——统计卡管数字，这个管内容，
   * 所以卡头与内边距只有这一处定义，视图不自己拼边框与标题行。
   * v3.8 起只有标准密度：右栏三张窄卡（曾用 `dense`）已随下载页单栏版式移除。
   */
  import type { Component, Snippet } from "svelte";
  import Badge from "./Badge.svelte";

  interface Props {
    title: string;
    hint?: string;
    icon?: Component;
    tone?: SectionTone;
    /** 卡头的计数药丸（如「下载队列」正在跑几项）；为 0 时不渲染。 */
    badge?: number;
    actions?: Snippet;
    children: Snippet;
    class?: string;
  }

  let {
    title,
    hint,
    icon: Icon,
    tone = "plain",
    badge,
    actions,
    children,
    class: className = "",
  }: Props = $props();

  const brand = $derived(tone === "brand");
</script>

<section class="card overflow-hidden {className}">
  <header
    class="flex items-center gap-2.5 px-4 py-3 md:px-5 {brand
      ? 'surface-brand'
      : 'border-b border-border'}"
  >
    {#if Icon}
      <span
        class="grid size-8 shrink-0 place-items-center rounded-chip {brand
          ? 'bg-primary-foreground/15'
          : 'bg-primary-soft text-primary'}"
        aria-hidden="true"
      >
        <Icon class="size-4" />
      </span>
    {/if}

    <div class="flex min-w-0 flex-1 flex-col">
      <div class="flex items-center gap-2">
        <h2 class="truncate text-h2 font-semibold">{title}</h2>
        {#if badge !== undefined}
          <Badge count={badge} />
        {/if}
      </div>
      {#if hint}
        <p
          class="truncate text-caption {brand
            ? 'text-primary-foreground/80'
            : 'text-muted-foreground'}"
        >
          {hint}
        </p>
      {/if}
    </div>

    {#if actions}
      <div class="flex shrink-0 items-center gap-1">{@render actions()}</div>
    {/if}
  </header>

  <div class="p-4 md:p-5">{@render children()}</div>
</section>
