<script lang="ts">
  /** 分区卡（设计规范 §5.7）：卡头（可选图标 + 标题 + 说明 + 右侧动作）+ 卡体。
   *
   * 它是「一屏内的一个分区」的统一外壳——统计卡管数字，这个管内容，
   * 所以卡头与内边距只有这一处定义，视图不自己拼边框与标题行。
   */
  import type { Component, Snippet } from "svelte";
  import Badge from "./Badge.svelte";

  interface Props {
    title: string;
    hint?: string;
    icon?: Component;
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
    badge,
    actions,
    children,
    class: className = "",
  }: Props = $props();
</script>

<section class="card overflow-hidden {className}">
  <header class="flex items-center gap-2.5 border-b border-border px-4 py-3 md:px-5">
    {#if Icon}
      <span
        class="grid size-8 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
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
        <p class="truncate text-caption text-muted-foreground">
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
