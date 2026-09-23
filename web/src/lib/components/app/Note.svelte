<script lang="ts">
  /** 行内提示：错误说清原因与修复，成功说清改了什么。左侧 2px 色条即唯一装饰。 */
  import type { Snippet } from "svelte";
  import type { Tone } from "$lib/tone";

  interface Props {
    tone?: Tone;
    children: Snippet;
    class?: string;
  }

  let { tone = "idle", children, class: className = "" }: Props = $props();

  const BAR: Record<Tone, string> = {
    live: "border-l-lamp-live",
    done: "border-l-lamp-done",
    fail: "border-l-lamp-fail",
    wait: "border-l-lamp-wait",
    idle: "border-l-rule-strong",
  };
</script>

<p class="border-l-2 pl-2.5 text-small {BAR[tone]} {className}">{@render children()}</p>
