<script lang="ts">
  /** 状态灯：8px 圆点 + 词（设计规范 §8）。状态一律「灯 + 词」，禁止仅用颜色表达状态。
   *
   * 只有「下载中」在呼吸（.dot-pulse，1.6s），其余静态；灯色与文字色的唯一映射在 `$lib/tone`。
   * `title` 挂在这组「灯 + 词」上：词只有两三个字装不下原因时，完整说明在这里（音乐源卡的失败详情）。
   */
  import { TONE_DOT, TONE_TEXT, type Tone } from "$lib/tone";

  interface Props {
    tone: Tone;
    label?: string;
    /** 悬停补充说明；不给就不出 title 属性。 */
    title?: string;
    class?: string;
  }

  let { tone, label, title, class: className = "" }: Props = $props();
</script>

<span class="inline-flex items-center gap-2 {TONE_TEXT[tone]} {className}" {title}>
  <span
    class="size-2 shrink-0 rounded-full {TONE_DOT[tone]} {tone === 'live' ? 'dot-pulse' : ''}"
    aria-hidden="true"
  ></span>
  {#if label}<span class="text-caption">{label}</span>{/if}
</span>
