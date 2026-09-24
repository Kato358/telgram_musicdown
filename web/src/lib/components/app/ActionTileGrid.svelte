<script lang="ts" module>
  import type { Component } from "svelte";

  /** 入口块：站内跳转用 `href`（真 `<a>`，可中键新开），需要先做别的事用 `onselect`。 */
  export interface ActionItem {
    label: string;
    icon: Component;
    href?: string;
    onselect?: () => void;
  }
</script>

<script lang="ts">
  /** 入口块栅格（设计规范 §5.8）：2×2 白块，图标 + 文字，整块可点。
   *
   * 只有入口，没有状态：块里不放计数、不放进度——那些属于统计卡与列表。
   */
  import Link from "./Link.svelte";

  interface Props {
    items: ActionItem[];
    class?: string;
  }

  let { items, class: className = "" }: Props = $props();

  const TILE_CLASS =
    "ui-transition flex items-center gap-2.5 rounded-nav border border-border px-3 py-3 text-body font-medium hover:border-primary/30 hover:bg-primary-soft";
</script>

<div class="grid grid-cols-2 gap-2 {className}">
  {#each items as item (item.label)}
    {@const Icon = item.icon}
    {#if item.href}
      <Link href={item.href} class={TILE_CLASS}>
        <Icon class="size-4 shrink-0 text-primary" aria-hidden="true" />
        <span class="min-w-0 truncate">{item.label}</span>
      </Link>
    {:else}
      <button type="button" class="{TILE_CLASS} text-left" onclick={() => item.onselect?.()}>
        <Icon class="size-4 shrink-0 text-primary" aria-hidden="true" />
        <span class="min-w-0 truncate">{item.label}</span>
      </button>
    {/if}
  {/each}
</div>
