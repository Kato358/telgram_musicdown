<script lang="ts">
  /** 空态（设计规范 §6.1）：占位大图标 + 引导文案 + 出口按钮。
   *
   * 文案说清下一步动作，不卖萌、不空泛；插画不做（除了一个图标，不进图）。
   */
  import type { Component, Snippet } from "svelte";
  import MusicIcon from "@lucide/svelte/icons/music";

  interface Props {
    title: string;
    hint?: string;
    icon?: Component;
    /** 嵌在卡片里（表格体、列表体内）时用 `bare`：不画第二层卡片与边框。 */
    bare?: boolean;
    actions?: Snippet;
    class?: string;
  }

  let {
    title,
    hint,
    icon: Icon = MusicIcon,
    bare = false,
    actions,
    class: className = "",
  }: Props = $props();

  const shell = $derived(
    bare
      ? "flex flex-col items-center gap-2 py-6 text-center"
      : "card flex flex-col items-center gap-2 px-4 py-10 text-center md:px-6 md:py-12",
  );
</script>

<div class="{shell} {className}">
  <Icon class="size-12 text-faint-foreground" aria-hidden="true" />
  <p class="text-body font-medium">{title}</p>
  {#if hint}
    <p class="max-w-[40ch] text-body text-muted-foreground">{hint}</p>
  {/if}
  {#if actions}
    <div class="mt-2 flex flex-wrap items-center justify-center gap-2">{@render actions()}</div>
  {/if}
</div>
