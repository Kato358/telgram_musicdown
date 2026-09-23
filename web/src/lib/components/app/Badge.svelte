<script lang="ts">
  /** 计数药丸：导航项与卡片头的计数（设计规范 §5.1）。
   *
   * 它是计数，不是状态灯——所以用浅绿底 + 主色字，不用 --lamp-*；
   * 为 0 时不渲染（不占位，也不显示「0」）。
   */
  interface Props {
    count: number;
    class?: string;
  }

  let { count, class: className = "" }: Props = $props();
</script>

{#if count > 0}
  <span
    class="tabular inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-primary-soft px-1.5 text-caption font-medium text-primary {className}"
  >
    {count}
  </span>
{/if}
