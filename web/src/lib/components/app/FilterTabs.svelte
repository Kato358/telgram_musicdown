<script lang="ts" module>
  /** 页签药丸（设计规范 §5.4）：卡片头的筛选面。
   *
   * 它是筛选，不是导航——所以是 `<button role="tab">` 而不是链接；计数跟在词后（`0` 也照写，
   * 它回答的是「这一类现在有多少条」，不写就分不清「没有」与「没查」）。
   */
  export interface TabItem {
    key: string;
    label: string;
    count?: number;
  }
</script>

<script lang="ts">
  interface Props {
    items: TabItem[];
    value: string;
    onchange: (key: string) => void;
    /** 组的无障碍名（如「按状态筛选」）。 */
    label: string;
    class?: string;
  }

  let { items, value, onchange, label, class: className = "" }: Props = $props();

  const BASE =
    "ui-transition inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-caption font-medium";
  const ON = "bg-primary text-primary-foreground";
  const OFF =
    "border border-border bg-card text-muted-foreground hover:bg-rule hover:text-foreground";
</script>

<div class="flex flex-wrap items-center gap-1 {className}" role="tablist" aria-label={label}>
  {#each items as item (item.key)}
    <button
      type="button"
      role="tab"
      class="{BASE} {item.key === value ? ON : OFF}"
      aria-selected={item.key === value}
      onclick={() => onchange(item.key)}
    >
      {item.label}
      {#if item.count !== undefined}
        <span class="tabular opacity-75">{item.count}</span>
      {/if}
    </button>
  {/each}
</div>
