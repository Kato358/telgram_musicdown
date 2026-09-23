<script lang="ts">
  /** 播放条：全局唯一 `<audio>`（SDD §5.4）。这里是全站唯一"填空"的表面，
   *  也是梯级读数从进度条延伸成位置指示器的地方（设计规范 §4）。 */
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import SkipBackIcon from "@lucide/svelte/icons/skip-back";
  import SkipForwardIcon from "@lucide/svelte/icons/skip-forward";
  import Volume2Icon from "@lucide/svelte/icons/volume-2";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n/index.svelte";
  import { formatDuration } from "$lib/format";
  import { player } from "$lib/stores/player.svelte";
  import Ladder from "./Ladder.svelte";

  let audio = $state<HTMLAudioElement | undefined>();
  let src = $state("");

  // 切歌：先 pause 旧实例再换 src；播放状态由 store 驱动（SDD §5.4）
  $effect(() => {
    const element = audio;
    const track = player.current;
    if (!element) return;
    if (!track) {
      element.pause();
      element.removeAttribute("src");
      src = "";
      return;
    }
    if (track.streamUrl !== src) {
      element.pause();
      src = track.streamUrl;
      element.src = track.streamUrl;
      element.load();
    }
    element.volume = player.volume;
    if (player.playing) {
      void element.play().catch(() => player.fail(t("player.unplayable")));
    } else {
      element.pause();
    }
  });

  // 位置回写：仅在明显偏离时写 currentTime，避免与 timeupdate 相互覆盖
  $effect(() => {
    const element = audio;
    if (!element || player.duration <= 0) return;
    if (Math.abs(element.currentTime - player.currentTime) > 0.4) {
      element.currentTime = player.currentTime;
    }
  });
</script>

<footer
  class="flex h-16 shrink-0 items-center gap-3 border-t border-rule-strong bg-card px-3 lg:gap-4 lg:px-5"
>
  <audio
    bind:this={audio}
    preload="metadata"
    ontimeupdate={() => audio && (player.currentTime = audio.currentTime)}
    ondurationchange={() => audio && (player.duration = audio.duration || 0)}
    onended={() => player.next()}
    onwaiting={() => (player.buffering = true)}
    onplaying={() => (player.buffering = false)}
    onerror={() => player.current && player.fail(t("player.unplayable"))}
  ></audio>

  <div class="flex min-w-0 flex-1 flex-col lg:w-56 lg:flex-none">
    {#if player.current}
      <span class="truncate text-small font-medium">{player.current.title}</span>
      {#if player.error}
        <span class="truncate text-micro text-lamp-fail">{player.error}</span>
      {:else}
        <span class="truncate text-micro text-muted-foreground">
          {player.current.artist ?? t("common.unknown")}
          {player.buffering ? ` · ${t("player.buffering")}` : ""}
        </span>
      {/if}
    {:else}
      <span class="text-small text-muted-foreground">{t("player.idle")}</span>
      <span class="hidden truncate text-micro text-muted-foreground sm:inline">
        {t("player.idleHint")}
      </span>
    {/if}
  </div>

  <div class="flex items-center gap-1">
    <Button
      variant="ghost"
      size="icon-sm"
      disabled={!player.hasPrev}
      aria-label={t("player.prev")}
      onclick={() => player.prev()}
    >
      <SkipBackIcon />
    </Button>
    <Button
      size="icon"
      class="rounded-full"
      disabled={!player.current}
      aria-label={player.playing ? t("player.pause") : t("player.play")}
      onclick={() => player.toggle()}
    >
      {#if player.playing}
        <PauseIcon />
      {:else}
        <PlayIcon />
      {/if}
    </Button>
    <Button
      variant="ghost"
      size="icon-sm"
      disabled={!player.hasNext}
      aria-label={t("player.next")}
      onclick={() => player.next()}
    >
      <SkipForwardIcon />
    </Button>
  </div>

  <span class="tabular hidden text-micro text-muted-foreground sm:inline">
    {formatDuration(player.currentTime)}
  </span>

  <div class="hidden min-w-0 flex-1 sm:block">
    <Ladder
      interactive
      segments={56}
      height={16}
      tone="live"
      ratio={player.current === null ? 0 : player.duration > 0 ? player.ratio : null}
      label={t("player.position")}
      onSeek={(ratio) => player.seekRatio(ratio)}
    />
  </div>

  <span class="tabular hidden text-micro text-muted-foreground sm:inline">
    {formatDuration(player.duration)}
  </span>

  <div class="hidden w-28 shrink-0 items-center gap-2 lg:flex">
    <Volume2Icon class="size-4 shrink-0 text-muted-foreground" />
    <Ladder
      interactive
      segments={10}
      height={12}
      tone="live"
      ratio={player.volume}
      label={t("player.volume")}
      onSeek={(ratio) => (player.volume = ratio)}
    />
  </div>
</footer>
