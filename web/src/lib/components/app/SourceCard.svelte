<script lang="ts">
  /** 音乐源卡（设计规范 §5.7 分区卡内的元素）：一个源一张卡——标题 / 身份行 / 状态灯 + 开关；
   *  频道源右上角一个 ×（移除），**在线平台没有 ×**：平台是内建的，只能启停。
   *
   *  卡面宽高恒为黄金比（`aspect-[1.618]`）：1100px 主栏下 6 列 = 160 × 99。窄屏（≤640px）
   *  退回单列列表——一张 320px 宽的卡按比例会撑成 200px 高的空壳，那里要的是密度不是比例。
   *
   *  状态只由一条规则决定，不是三处分支：**启停优先于错误**。关闭的源不参与搜索，
   *  「源无法访问」也就无从谈起——关掉即回到「已停用」，打开错误才重新显形。
   */
  import XIcon from "@lucide/svelte/icons/x";
  import { t } from "$lib/i18n/index.svelte";
  import type { Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Switch } from "$lib/components/ui/switch";
  import Lamp from "./Lamp.svelte";

  interface Props {
    /** 开关的 id：页内唯一。 */
    switchId: string;
    title: string;
    /** 副行：频道是 @句柄，在线源是供应商名。 */
    meta: string;
    /** 副行是否走等宽——句柄与 id 是机器数据才等宽（设计规范 §4.2）。 */
    mono?: boolean;
    /** 标题悬停的完整身份（长名 + id），缺省就是标题本身。 */
    tooltip?: string;
    enabled: boolean;
    /** 失败详情：给了就把灯切红、词换成「保存失败」，原因与修复收在 title 里。 */
    error?: string | null;
    /** 是否渲染右上角 × ——在线源传 false。 */
    removable?: boolean;
    /** 写库失败时 +1 强制重挂载开关（Switch 没有 bind 时会把点击结果留在本地覆盖值里）。 */
    epoch?: number;
    onToggle: (checked: boolean) => void;
    onRemove?: () => void;
  }

  let {
    switchId,
    title,
    meta,
    mono = false,
    tooltip,
    enabled,
    error = null,
    removable = false,
    epoch = 0,
    onToggle,
    onRemove,
  }: Props = $props();

  /** 只有「启用中且出错」才走失败态：关掉就没有错误可言。 */
  const failed = $derived(enabled && Boolean(error));

  const state = $derived.by((): { tone: Tone; word: string; hint: string } => {
    if (!enabled) {
      return { tone: "idle", word: t("sources.stateOff"), hint: t("sources.hintOff") };
    }
    if (error) {
      return { tone: "fail", word: t("sources.stateFailed"), hint: error };
    }
    return { tone: "done", word: t("sources.stateOn"), hint: t("sources.hintOn") };
  });
</script>

<li
  class="group relative flex aspect-[1.618] min-w-0 flex-col justify-between gap-[5px] rounded-lg border bg-card p-[11px_12px_12px] shadow-xs transition-[border-color,box-shadow] duration-[130ms] ease-out max-[640px]:aspect-auto {failed
    ? 'border-destructive/40 has-[:focus-visible]:border-ring'
    : 'border-border hover:border-primary/25 has-[:focus-visible]:border-ring'}"
>
  {#if removable}
    <!-- 24px 的字形配 28px 的点按区（icon-sm）；静止时克制，指到卡上先亮一档 -->
    <Button
      variant="ghost"
      size="icon-sm"
      class="absolute right-1 top-[7px] text-muted-foreground opacity-50 hover:bg-destructive/10 hover:text-destructive-text hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-85"
      onclick={() => onRemove?.()}
      aria-label={t("sources.remove")}
      title={t("sources.remove")}
    >
      <XIcon />
    </Button>
  {/if}

  <h3
    class="truncate pr-7 text-body font-medium {enabled ? '' : 'text-muted-foreground'}"
    title={tooltip ?? title}
  >
    {title}
  </h3>

  <p class="truncate {mono ? 'text-code' : 'text-caption'} text-muted-foreground">{meta}</p>

  <div class="mt-1 flex items-center justify-between gap-2">
    <Lamp tone={state.tone} label={state.word} title={state.hint} />
    {#key epoch}
      <Switch
        id={switchId}
        checked={enabled}
        aria-label={t("sources.enabled")}
        onCheckedChange={(checked) => onToggle(checked)}
      />
    {/key}
  </div>
</li>
