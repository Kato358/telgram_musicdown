<script lang="ts">
  /** 状态灯：8px 圆点 + 文字。状态一律「灯 + 词」，不做彩色徽章块（设计规范 §5）。 */
  import { TONE_DOT, TONE_TEXT, type Tone } from "$lib/tone";

  interface Props {
    tone: Tone;
    label?: string;
    /** 灯在文字之后。 */
    reverse?: boolean;
    class?: string;
  }

  let { tone, label, reverse = false, class: className = "" }: Props = $props();
</script>

<span class="inline-flex items-center gap-1.5 {TONE_TEXT[tone]} {className}">
  {#if reverse && label}<span class="text-small">{label}</span>{/if}
  <span class="size-2 shrink-0 rounded-full {TONE_DOT[tone]}" aria-hidden="true"></span>
  {#if !reverse && label}<span class="text-small">{label}</span>{/if}
</span>
