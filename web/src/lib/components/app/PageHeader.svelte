<script lang="ts">
  /** 页头：标题 + 一行上下文 + 右侧动作/计数；下方一条规则线，与内容区分开。 */
  import type { Snippet } from "svelte";

  interface Props {
    title: string;
    lede?: string;
    aside?: Snippet;
  }

  let { title, lede, aside }: Props = $props();
</script>

<header class="mb-5 flex items-end justify-between gap-4 border-b border-rule pb-3">
  <div class="min-w-0">
    <h1 class="text-title font-semibold">{title}</h1>
    {#if lede}
      <p class="mt-1 text-small text-muted-foreground">{lede}</p>
    {/if}
  </div>
  {#if aside}
    <div class="flex shrink-0 items-center gap-2">{@render aside()}</div>
  {/if}
</header>
