<script lang="ts">
  /** APlayer 宿主：全站唯一的播放器（吸底模式）挂在这里。
   *
   * 旧的 76px 播放条已撤，播放 UI 就是 APlayer 自己的那一套（封面 / 控制 / 列表）；
   * 这个组件只负责给它一个容器、随主题同步主色。外壳常驻，播放器也就常驻。
   */
  import { onMount } from "svelte";
  import { t } from "$lib/i18n/index.svelte";
  import { player } from "$lib/stores/player.svelte";

  let host = $state<HTMLDivElement>();

  /** 离开页面（整页导航 / 刷新 / 关标签）前把在播的音频停住。 */
  const onPageHide = () => player.pause();

  onMount(() => {
    if (host) player.mount(host);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      player.unmount();
    };
  });

  // applyTheme 内部读 resolved：主题（含跟随系统的翻面）一变就重刷播放器主色
  $effect(() => {
    player.applyTheme();
  });
</script>

<div class="aplayer-host" bind:this={host} role="region" aria-label={t("player.title")}></div>
