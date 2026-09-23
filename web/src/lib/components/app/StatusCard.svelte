<script lang="ts">
  /** 状态与信息卡（设计规范 §5.3）：底 --primary-surface，浅绿只做底、不做文字色。
   *
   * 内部结构自上而下：状态图标 40px 圆 / 标题 18px·600 / 说明 14px muted / 动作（主按钮、
   * 快捷标签、读数行） / 路径框。空态即此卡的常态。
   */
  import type { Snippet } from "svelte";
  import PathText from "./PathText.svelte";

  interface Props {
    icon: Snippet;
    title: string;
    hint?: string;
    path?: string | null;
    pathMissing?: string | null;
    children?: Snippet;
    class?: string;
  }

  let {
    icon,
    title,
    hint,
    path = null,
    pathMissing = null,
    children,
    class: className = "",
  }: Props = $props();
</script>

<section class="flex flex-col gap-4 rounded-card bg-primary-surface p-4 md:p-6 {className}">
  <span
    class="grid size-10 shrink-0 place-items-center rounded-full bg-primary-soft text-primary"
    aria-hidden="true"
  >
    {@render icon()}
  </span>

  <div class="flex flex-col gap-1">
    <h2 class="text-h2 font-semibold">{title}</h2>
    {#if hint}
      <p class="max-w-[40ch] text-body text-muted-foreground">{hint}</p>
    {/if}
  </div>

  {@render children?.()}

  {#if path || pathMissing}
    <PathText variant="box" {path} missing={pathMissing} />
  {/if}
</section>
