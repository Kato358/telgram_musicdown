<script lang="ts">
  /** 行内提示（设计规范 §6.2）：紧贴触发控件，左侧 2px 色条即唯一装饰，不用 toast。
   *
   * 错误说原因与修复（用 --destructive-text 的 4.98:1），成功说清改了什么。
   */
  import type { Snippet } from "svelte";
  import { TONE_BAR, type Tone } from "$lib/tone";

  interface Props {
    tone?: Tone;
    children: Snippet;
    class?: string;
  }

  let { tone = "idle", children, class: className = "" }: Props = $props();
</script>

<p
  class="border-l-2 py-0.5 pl-2.5 text-caption {TONE_BAR[tone]} {tone === 'fail'
    ? 'text-destructive-text'
    : ''} {className}"
>
  {@render children()}
</p>
