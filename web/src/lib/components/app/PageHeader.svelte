<script lang="ts">
  /** 页头：主页面大标题 + 一行上下文，右侧放计数/动作（设计规范 §2.1）。 */
  import type { Snippet } from "svelte";

  interface Props {
    title: string;
    lede?: string;
    aside?: Snippet;
  }

  let { title, lede, aside }: Props = $props();
</script>

<header class="flex flex-wrap items-end justify-between gap-3">
  <div class="min-w-0">
    <h1 class="text-h1 font-bold">{title}</h1>
    {#if lede}
      <p class="mt-1 max-w-[80ch] text-body text-muted-foreground">{lede}</p>
    {/if}
  </div>
  {#if aside}
    <div class="flex shrink-0 items-center gap-2">{@render aside()}</div>
  {/if}
</header>
