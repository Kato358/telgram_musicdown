<script lang="ts">
  /** 音质选择弹窗（SDD §2.7）：点在线源行的「下载」先问档位，再入队。
   *
   *  阶梯是**服务端发的语义档位**（128k/320k/无损/Hi-Res/母带），不是上游原生值：
   *  同一档在网易叫 exhigh、QQ 叫 320k，映射留在后端一处（app/chksz/quality.py），
   *  前端维护第二份必然漂。加新平台也不用改这个组件。
   *
   *  同一个弹窗也服务「浏览器下载」（FR-DL-08）：档位阶梯完全相同，只是选完之后
   *  落到哪儿不同（入队落盘 / 存到本机浏览器下载目录），故确认文案与提示按
   *  `search.qualityIntent` 分岔，不另开一个几乎一样的弹窗。
   *
   *  频道行不走这里——频道里的文件就是它本身，没有「档」可选。
   */
  import { search } from "$lib/stores/search.svelte";
  import { t } from "$lib/i18n/index.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Dialog from "$lib/components/ui/dialog";
  import type { QualityOption } from "$lib/api/types";

  interface Props {
    onconfirm: () => void;
  }

  let { onconfirm }: Props = $props();

  const target = $derived(search.qualityTarget);
  const options = $derived<QualityOption[]>(
    target === null ? [] : (search.ladders[target.provider] ?? []),
  );
</script>

<Dialog.Root
  open={target !== null}
  onOpenChange={(open) => {
    if (!open) search.qualityTarget = null;
  }}
>
  <Dialog.Portal>
    <Dialog.Overlay />
    <Dialog.Content class="sm:max-w-sm">
      <Dialog.Header>
        <Dialog.Title>{t("search.qualityTitle")}</Dialog.Title>
        <Dialog.Description>
          {#if target}
            {target.title}{target.artist ? ` · ${target.artist}` : ""}
          {/if}
        </Dialog.Description>
      </Dialog.Header>

      <div class="flex flex-col gap-1">
        {#each options as option (option.tier)}
          <label
            class="ui-transition flex cursor-pointer items-center gap-3 rounded-control px-3 py-2.5 text-body hover:bg-rule has-checked:bg-primary-soft"
          >
            <input
              type="radio"
              name="tgm-quality"
              class="accent-primary"
              value={option.tier}
              checked={search.qualityChoice === option.tier}
              onchange={() => (search.qualityChoice = option.tier)}
            />
            <span class="flex-1">{option.label}</span>
            {#if option.best}
              <span
                class="rounded-full bg-primary-soft px-2 py-0.5 text-caption text-primary"
              >
                {t("search.qualityBest")}
              </span>
            {/if}
          </label>
        {/each}
      </div>

      <p class="text-caption text-faint-foreground">
        {search.qualityIntent === "browser" ? t("search.qualityHintBrowser") : t("search.qualityHint")}
      </p>

      <Dialog.Footer>
        <Dialog.Close>
          <Button variant="ghost">{t("common.cancel")}</Button>
        </Dialog.Close>
        <Button onclick={onconfirm}>
          {search.qualityIntent === "browser"
            ? t("search.qualityConfirmBrowser")
            : t("search.qualityConfirm")}
        </Button>
      </Dialog.Footer>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>
