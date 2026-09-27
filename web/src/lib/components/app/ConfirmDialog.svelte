<script lang="ts">
  /** 破坏性二次确认弹窗（设计规范 §5.9）：全站八处确认共用这一副骨架——
   *  危险徽标 / 标题 / 后果说明 / 对象卡 / 保留清单 /（可选勾选整行、失败提示）/ 取消 + 危险确认。
   *
   *  三条分工写死在结构里，调用方按槽位填，不在视图里各写各的：
   *  - **标题只问动作**（「删除这首音乐？」），被删的那个东西由 `target` 说出，长名字不再把标题挤成两行；
   *  - **`keep` 说不会被动到的东西**（曲库文件 / 历史记录 / 音乐源），「会删什么」与「不会删什么」分开；
   *  - 确认键是**实心红**（`--destructive-text` 底 + `--card` 字，浅色 4.98:1 / 深色 7.26:1），
   *    与取消键同高，危险靠颜色而不是靠体量；初始焦点落在取消上。
   */
  import TriangleAlertIcon from "@lucide/svelte/icons/triangle-alert";
  import CheckIcon from "@lucide/svelte/icons/check";
  import { t } from "$lib/i18n/index.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogDescription, DialogTitle } from "$lib/components/ui/dialog";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import Note from "$lib/components/app/Note.svelte";

  interface Props {
    open?: boolean;
    title: string;
    /** 只说后果，不复述标题里的名字。 */
    description?: string | null;
    /** 被删/被移除的那一个东西：名字 + 随它一起的事实（大小、时间、身份）。 */
    target?: { name: string; meta?: string | null; mono?: boolean } | null;
    /** 不会被动到的东西，逐行一条。 */
    keep?: string[] | null;
    /** 可选的连带动作（如「同时删除历史记录」）：整行可点，勾选值走 `bind:optionChecked`。 */
    option?: { label: string; hint?: string | null } | null;
    optionChecked?: boolean;
    error?: string | null;
    confirmLabel: string;
    /** 进行中时确认键上的字；缺省沿用 confirmLabel（只转圈）。 */
    pendingLabel?: string | null;
    pending?: boolean;
    onconfirm: () => void;
    /** 弹窗关闭（取消 / Esc / 点遮罩 / 确认后）时清理调用方的目标对象。 */
    onclosed?: () => void;
  }

  let {
    open = $bindable(false),
    title,
    description = null,
    target = null,
    keep = null,
    option = null,
    optionChecked = $bindable(false),
    error = null,
    confirmLabel,
    pendingLabel = null,
    pending = false,
    onconfirm,
    onclosed = () => {},
  }: Props = $props();

  let cancelRef = $state<HTMLButtonElement | null>(null);

  /** 危险确认键：走 outline variant 再覆写配色——default variant 挂着 `.btn-primary` 的主色投影，
   *  红键上会泛绿；而 `ui/` 是 shadcn 生成物，不在生成物里另加变体。 */
  const DANGER =
    "border-transparent bg-destructive-text px-3.5 text-card hover:bg-[color-mix(in_oklab,var(--destructive-text)_88%,var(--foreground))] hover:text-card dark:bg-destructive-text dark:hover:bg-destructive-text";
</script>

<Dialog
  bind:open
  onOpenChange={(next) => {
    if (!next) onclosed?.();
  }}
>
  <!-- `role` 由 bits-ui 的 Dialog 根固定成 "dialog"（state.props 覆盖 restProps），不要在这里传
       alertdialog——传了也不生效。真要那一档得换 AlertDialog 原语或另立 ui/alert-dialog 包装，
       代价是再抄一份弹窗样式；当前 a11y 由 aria-modal + 标题/说明绑定承担。 -->
  <DialogContent
    class="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-sm"
    onOpenAutoFocus={(e) => {
      e.preventDefault();
      cancelRef?.focus();
    }}
  >
    <div class="flex gap-3.5 px-5 pt-5">
      <span
        class="grid size-10 shrink-0 place-items-center rounded-full bg-destructive/12 text-destructive-text"
      >
        <TriangleAlertIcon class="size-5" />
      </span>
      <div class="min-w-0 flex-1 pr-5">
        <DialogTitle class="text-h2 leading-[1.4] font-semibold">{title}</DialogTitle>
        {#if description}
          <DialogDescription class="mt-1 text-body text-muted-foreground"
            >{description}</DialogDescription
          >
        {/if}
      </div>
    </div>

    <div class="flex min-h-0 flex-col gap-3 overflow-y-auto px-5 py-4">
      {#if target}
        <div class="rounded-control border border-rule bg-surface-subtle px-3 py-2.5">
          <p class="text-body font-medium break-all {target.mono ? 'text-code' : ''}">
            {target.name}
          </p>
          {#if target.meta}
            <p class="tabular mt-0.5 text-caption break-all text-muted-foreground">
              {target.meta}
            </p>
          {/if}
        </div>
      {/if}

      {#if keep && keep.length > 0}
        <ul class="flex flex-col gap-1">
          {#each keep as line (line)}
            <li class="flex gap-2 text-body text-muted-foreground">
              <CheckIcon class="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden="true" />
              <span>{line}</span>
            </li>
          {/each}
        </ul>
      {/if}

      {#if option}
        <label
          class="flex cursor-pointer items-start gap-2.5 rounded-control border border-border bg-popover px-3 py-2.5 transition-colors hover:bg-surface-subtle"
        >
          <Checkbox bind:checked={optionChecked} class="mt-0.5" />
          <span class="min-w-0">
            <span class="block text-body font-medium">{option.label}</span>
            {#if option.hint}
              <span class="mt-0.5 block text-caption text-muted-foreground">{option.hint}</span>
            {/if}
          </span>
        </label>
      {/if}

      {#if error}
        <Note tone="fail">{error}</Note>
      {/if}
    </div>

    <div class="flex justify-end gap-2 border-t border-border bg-surface-subtle px-4 py-2.5">
      <Button bind:ref={cancelRef} variant="outline" class="px-3.5" onclick={() => (open = false)}>
        {t("common.cancel")}
      </Button>
      <Button variant="outline" class={DANGER} disabled={pending} onclick={onconfirm}>
        {#if pending}
          <span
            class="size-3.5 shrink-0 animate-spin rounded-full border-2 border-card/35 border-t-card"
            aria-hidden="true"
          ></span>
          {pendingLabel ?? confirmLabel}
        {:else}
          {confirmLabel}
        {/if}
      </Button>
    </div>
  </DialogContent>
</Dialog>
