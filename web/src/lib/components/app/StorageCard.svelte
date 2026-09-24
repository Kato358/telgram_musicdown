<script lang="ts">
  /** 右栏「存储空间」（设计规范 §5.7）：库在磁盘上的占用，一条卡一句话。
   *
   * 参考图把它画成独立小卡：卡头是图标 + 名称，右侧是「已用 / 上限」。上限未设
   * （`quota = null`，本系统的默认）时不画进度条——没有分母的进度条是假的（§3.4），
   * 卡体退成一行说明；上限有值时进度条 = `bytes / quota`。
   */
  import DatabaseIcon from "@lucide/svelte/icons/database";
  import { formatSize } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import ProgressBar from "./ProgressBar.svelte";
  import SectionCard from "./SectionCard.svelte";

  interface Props {
    /** 磁盘上的实际占用（`stats.library.bytes`）。 */
    bytes: number | null;
    /** 存储上限；null = 未设上限。 */
    quota: number | null;
    class?: string;
  }

  let { bytes, quota, class: className = "" }: Props = $props();

  const ratio = $derived(
    bytes === null || quota === null || quota <= 0 ? null : Math.min(bytes / quota, 1),
  );
</script>

<SectionCard title={t("downloads.storageLabel")} icon={DatabaseIcon} dense class={className}>
  {#snippet actions()}
    <span class="tabular text-caption text-muted-foreground">
      {formatSize(bytes)}
      /
      {quota === null ? t("downloads.storageUnlimited") : formatSize(quota)}
    </span>
  {/snippet}

  {#if ratio === null}
    <p class="text-caption text-muted-foreground">{t("downloads.storageHint")}</p>
  {:else}
    <ProgressBar {ratio} label={t("downloads.storageLabel")} />
  {/if}
</SectionCard>
