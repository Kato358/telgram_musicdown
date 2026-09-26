<script lang="ts">
  /** 落盘路径（设计规范 §3.4 / §5.3）：路径是产品承诺的物证，是列表行里的一级文本。
   *
   * - 目录段可截断，文件名段永不被省略号吃掉——扩展名是曲库的判断依据。
   * - 文件不在磁盘时整行换成原因文案（错误不只靠红色，必须带文字原因）。
   */
  import CircleAlertIcon from "@lucide/svelte/icons/circle-alert";
  import { splitPath } from "$lib/format";

  interface Props {
    path: string | null;
    /** 文件不在磁盘的原因文案；给了就整行替换路径。 */
    missing?: string | null;
    class?: string;
  }

  let { path, missing = null, class: className = "" }: Props = $props();

  const parts = $derived(path ? splitPath(path) : null);
</script>

{#if missing}
  <p class="flex min-w-0 items-center gap-1.5 text-code text-destructive-text {className}">
    <CircleAlertIcon class="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
    <span class="truncate">{missing}</span>
  </p>
{:else if parts}
  <p
    class="flex min-w-0 items-baseline text-code text-muted-foreground {className}"
    title={path ?? ""}
  >
    <!-- 目录段 min-w-0：没有它，flex 子项的 min-content 是整段目录文本，
         窄列里目录根本不会截断，会把整行撑出卡片（v3.7 修）。 -->
    <span class="min-w-0 truncate">{parts.dir}</span>
    <span class="shrink-0">{parts.file}</span>
  </p>
{:else}
  <p class="text-code text-faint-foreground {className}">—</p>
{/if}
