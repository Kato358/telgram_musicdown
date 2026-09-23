<script lang="ts" module>
  /** 表格列：表头与数据行引用同一份类串，列宽因此天然对齐（设计规范 §5.4）。 */
  export interface Column {
    key: string;
    label: string;
    /** 列宽/对齐类；表头与行都用它。 */
    class: string;
  }

  /** 数据行外壳：高 60px（窄屏 56px）、1px 分割线、整行 hover 底 --rule。 */
  export const ROW_CLASS =
    "flex h-14 items-center gap-3 border-b border-rule px-4 ui-transition last:border-b-0 md:h-[60px] md:px-6";
</script>

<script lang="ts">
  /** 数据表容器（设计规范 §5.4）：卡片化容器 + 表头，行由调用方以 `<li>` 提供。
   *
   * 容器内边距为 0：表头与行自带 24px 内边距，所以分割线通到卡片边缘。
   */
  import type { Snippet } from "svelte";

  interface Props {
    columns: Column[];
    /** 卡片标题行（标题 + 计数 + 「查看全部」），渲染在列标签之上。 */
    header?: Snippet;
    children: Snippet;
    class?: string;
  }

  let { columns, header, children, class: className = "" }: Props = $props();
</script>

<div class="card overflow-hidden {className}">
  {#if header}
    <div class="flex h-14 items-center gap-3 border-b border-border bg-card px-4 md:px-6">
      {@render header()}
    </div>
  {/if}
  <div
    class="flex h-10 items-center gap-3 border-b border-border bg-surface-subtle px-4 text-caption text-muted-foreground md:px-6"
  >
    {#each columns as column (column.key)}
      <span class={column.class}>{column.label}</span>
    {/each}
  </div>
  <ul>
    {@render children()}
  </ul>
</div>
