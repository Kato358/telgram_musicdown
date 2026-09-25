<script lang="ts">
  /** 底部播放条（设计规范 §5.5）：76px，三分区（信息 / 控制 + 进度 / 音量 + 队列）。
   *
   * 全站唯一 `<audio>`（SDD §5.4）：切歌先 pause() 再换 src。
   * 进度有两种皮肤——线型与波形——同一份比例、同一个 range 内核，只是换皮肤；
   * 偏好在右侧的图标键切换，存本机（`tgm-player-progress`）。
   * v3.9：播放中左侧封面放大到 56px 并亮起主色渐变 + 柔光 + 动态频谱小图标，
   * 让「正在响」这件事在播放条上有一眼可见的存在感。
   */
  import AudioWaveformIcon from "@lucide/svelte/icons/audio-waveform";
  import ListMusicIcon from "@lucide/svelte/icons/list-music";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import RepeatIcon from "@lucide/svelte/icons/repeat";
  import ShuffleIcon from "@lucide/svelte/icons/shuffle";
  import SkipBackIcon from "@lucide/svelte/icons/skip-back";
  import SkipForwardIcon from "@lucide/svelte/icons/skip-forward";
  import Volume2Icon from "@lucide/svelte/icons/volume-2";
  import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
  } from "$lib/components/ui/dropdown-menu";
  import { t } from "$lib/i18n/index.svelte";
  import { formatDuration } from "$lib/format";
  import { player } from "$lib/stores/player.svelte";
  import ProgressBar from "./ProgressBar.svelte";
  import WaveProgress from "./WaveProgress.svelte";

  let audio = $state<HTMLAudioElement | undefined>();
  let src = $state("");

  const ratio = $derived(
    player.current === null ? null : player.duration > 0 ? player.ratio : null,
  );
  const timecode = $derived(
    `${formatDuration(player.currentTime)} / ${formatDuration(player.duration)}`,
  );

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

  function toggleStyle() {
    player.setProgressStyle(player.progressStyle === "wave" ? "line" : "wave");
  }
</script>

<footer
  class="relative flex h-[76px] shrink-0 items-center gap-3 border-t border-border bg-card px-4 md:gap-4 md:px-6"
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

  <!-- 左：当前播放。播放中封面放大一档（48→56px）、主色渐变底 + 柔光，
       静态音符徽换成三根柱的动态频谱小图标（v3.9）。 -->
  <div class="flex min-w-0 flex-1 items-center gap-3 md:w-72 md:flex-none">
    <span
      class="ui-transition grid {player.playing
        ? 'size-14 surface-brand playing-glow'
        : 'size-12 bg-primary-soft text-primary'} shrink-0 place-items-center rounded-chip"
      aria-hidden="true"
    >
      {#if player.playing}
        <span class="eq"><i></i><i></i><i></i></span>
      {:else}
        <MusicIcon class="size-5" />
      {/if}
    </span>
    <div class="flex min-w-0 flex-1 flex-col">
      {#if player.current}
        <span class="truncate text-body font-semibold">{player.current.title}</span>
        {#if player.error}
          <span class="truncate text-caption text-destructive-text">{player.error}</span>
        {:else}
          <span
            class="truncate text-caption {player.current.artist
              ? 'text-muted-foreground'
              : 'text-faint-foreground'}"
          >
            {player.current.artist ?? t("common.placeholder")}{player.buffering
              ? ` · ${t("player.buffering")}`
              : ""}
          </span>
        {/if}
      {:else}
        <span class="truncate text-body text-muted-foreground">{t("player.idle")}</span>
        <span class="hidden truncate text-caption text-faint-foreground md:block">
          {t("player.idleHint")}
        </span>
      {/if}
    </div>
  </div>

  <!-- 中：控制 + 时间与进度 -->
  <div class="flex min-w-0 flex-col items-center gap-1 md:flex-1">
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="ui-transition hidden size-8 shrink-0 place-items-center rounded-full hover:bg-rule md:grid {player.shuffle
          ? 'text-primary'
          : 'text-muted-foreground hover:text-foreground'}"
        aria-label={t("player.shuffle")}
        aria-pressed={player.shuffle}
        onclick={() => (player.shuffle = !player.shuffle)}
      >
        <ShuffleIcon class="size-4" aria-hidden="true" />
      </button>

      <button
        type="button"
        class="ui-transition hidden size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 md:grid"
        aria-label={t("player.prev")}
        disabled={!player.hasPrev}
        onclick={() => player.prev()}
      >
        <SkipBackIcon class="size-4" aria-hidden="true" />
      </button>

      <button
        type="button"
        class="ui-transition mx-1 grid size-10 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
        aria-label={player.playing ? t("player.pause") : t("player.play")}
        disabled={!player.current}
        onclick={() => player.toggle()}
      >
        {#if player.playing}
          <PauseIcon class="size-5" aria-hidden="true" />
        {:else}
          <PlayIcon class="size-5" aria-hidden="true" />
        {/if}
      </button>

      <button
        type="button"
        class="ui-transition hidden size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 md:grid"
        aria-label={t("player.next")}
        disabled={!player.hasNext}
        onclick={() => player.next()}
      >
        <SkipForwardIcon class="size-4" aria-hidden="true" />
      </button>

      <button
        type="button"
        class="ui-transition hidden size-8 shrink-0 place-items-center rounded-full hover:bg-rule md:grid {player.repeat
          ? 'text-primary'
          : 'text-muted-foreground hover:text-foreground'}"
        aria-label={t("player.repeat")}
        aria-pressed={player.repeat}
        onclick={() => (player.repeat = !player.repeat)}
      >
        <RepeatIcon class="size-4" aria-hidden="true" />
      </button>
    </div>

    <div class="hidden w-full max-w-[560px] items-center gap-3 md:flex">
      <span class="tabular shrink-0 text-caption text-muted-foreground">
        {formatDuration(player.currentTime)}
      </span>
      {#if player.progressStyle === "wave"}
        <WaveProgress
          seed={player.current?.id ?? "idle"}
          {ratio}
          label={t("player.position")}
          valueText={timecode}
          interactive
          onseek={(value) => player.seekRatio(value)}
          class="flex-1"
        />
      {:else}
        <ProgressBar
          {ratio}
          label={t("player.position")}
          valueText={timecode}
          interactive
          onseek={(value) => player.seekRatio(value)}
          class="flex-1"
        />
      {/if}
      <span class="tabular shrink-0 text-caption text-faint-foreground">
        {formatDuration(player.duration)}
      </span>
    </div>
  </div>

  <!-- 右：进度外观 + 音量 + 队列 -->
  <div class="hidden w-60 shrink-0 items-center justify-end gap-1 md:flex">
    <button
      type="button"
      class="ui-transition grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground"
      aria-label={t("player.progressStyle")}
      onclick={toggleStyle}
    >
      {#if player.progressStyle === "wave"}
        <AudioWaveformIcon class="size-4" aria-hidden="true" />
      {:else}
        <MinusIcon class="size-4" aria-hidden="true" />
      {/if}
    </button>

    <Volume2Icon class="ml-1 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
    <ProgressBar
      ratio={player.volume}
      label={t("player.volume")}
      interactive
      onseek={(value) => player.setVolume(value)}
      class="w-24 shrink-0"
    />

    <DropdownMenu>
      <DropdownMenuTrigger>
        {#snippet child({ props })}
          <button
            {...props}
            type="button"
            class="ui-transition ml-1 grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground"
            aria-label={t("player.queue")}
          >
            <ListMusicIcon class="size-4" aria-hidden="true" />
          </button>
        {/snippet}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" class="max-h-72 min-w-64 overflow-y-auto">
        {#if player.queue.length === 0}
          <DropdownMenuItem disabled>{t("player.queueEmpty")}</DropdownMenuItem>
        {:else}
          {#each player.queue as track, index (track.id)}
            <DropdownMenuItem
              onSelect={() => player.play(player.queue, index)}
              class="justify-between gap-3 {index === player.index
                ? 'bg-primary-soft text-primary'
                : ''}"
            >
              <span class="min-w-0 truncate">{track.title}</span>
              <span class="tabular shrink-0 text-caption text-faint-foreground">{index + 1}</span>
            </DropdownMenuItem>
          {/each}
        {/if}
      </DropdownMenuContent>
    </DropdownMenu>
  </div>

  <!-- <768px：贴底 2px 进度，不可拖（§6.3） -->
  <div class="absolute inset-x-0 bottom-0 h-0.5 bg-progress-track md:hidden" aria-hidden="true">
    <div class="h-full bg-progress-fill" style="width: {player.ratio * 100}%"></div>
  </div>
</footer>
