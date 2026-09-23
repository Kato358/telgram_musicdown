<script lang="ts">
  /** 状态灯：8px 圆点 + 词（设计规范 §8）。状态一律「灯 + 词」，禁止仅用颜色表达状态。
   *
   * 只有「下载中」在呼吸（.dot-pulse，1.6s），其余静态；灯色与文字色的唯一映射在 `$lib/tone`。
   */
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

<span class="inline-flex items-center gap-2 {TONE_TEXT[tone]} {className}">
  {#if reverse && label}<span class="text-caption">{label}</span>{/if}
  <span
    class="size-2 shrink-0 rounded-full {TONE_DOT[tone]} {tone === 'live' ? 'dot-pulse' : ''}"
    aria-hidden="true"
  ></span>
  {#if !reverse && label}<span class="text-caption">{label}</span>{/if}
</span>
