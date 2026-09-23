<script lang="ts">
  /** 表单行：标签在上、控件在下、补充说明第三行。所有表单统一这一形状（设计规范 §2.2）。 */
  import type { Snippet } from "svelte";

  interface Props {
    label: string;
    for?: string;
    hint?: string;
    aside?: Snippet;
    children: Snippet;
    class?: string;
  }

  let { label, for: htmlFor, hint, aside, children, class: className = "" }: Props = $props();
</script>

<div class="flex flex-col gap-1.5 {className}">
  <div class="flex items-baseline justify-between gap-3">
    <label class="text-caption text-muted-foreground" for={htmlFor}>{label}</label>
    {#if aside}{@render aside()}{/if}
  </div>
  {@render children()}
  {#if hint}
    <p class="text-caption text-faint-foreground">{hint}</p>
  {/if}
</div>
