<script lang="ts">
  /** 空态：一句说明 + 一个出口动作。空屏是邀请，不是墓碑（设计规范 §6）。 */
  import type { Snippet } from "svelte";

  interface Props {
    title: string;
    hint?: string;
    actions?: Snippet;
  }

  let { title, hint, actions }: Props = $props();
</script>

<div class="border border-dashed border-rule-strong px-5 py-8">
  <p class="text-body font-medium">{title}</p>
  {#if hint}
    <p class="mt-1 max-w-[52ch] text-small text-muted-foreground">{hint}</p>
  {/if}
  {#if actions}
    <div class="mt-4 flex flex-wrap items-center gap-2">{@render actions()}</div>
  {/if}
</div>
