<script lang="ts">
  /** 当前播放（设计规范 §5.8）：底部播放条在仪表盘上的镜像。
   *
   * 全站仍然只有一个 `<audio>`（挂在 `TransportBar`）——这里的控制键只改 `player` store，
   * 不持有播放实例。它让「正在听什么」在一屏内可达，不用把视线挪到屏幕底部。
   */
  import AudioWaveformIcon from "@lucide/svelte/icons/audio-waveform";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import RepeatIcon from "@lucide/svelte/icons/repeat";
  import ShuffleIcon from "@lucide/svelte/icons/shuffle";
  import SkipBackIcon from "@lucide/svelte/icons/skip-back";
  import SkipForwardIcon from "@lucide/svelte/icons/skip-forward";
  import { t } from "$lib/i18n/index.svelte";
  import { formatDuration } from "$lib/format";
  import { player } from "$lib/stores/player.svelte";
  import Note from "./Note.svelte";
  import ProgressBar from "./ProgressBar.svelte";
  import SectionCard from "./SectionCard.svelte";

  const ratio = $derived(player.duration > 0 ? player.ratio : null);
  const timecode = $derived(
    `${formatDuration(player.currentTime)} / ${formatDuration(player.duration)}`,
  );

  const ICON_CLASS =
    "ui-transition grid size-8 shrink-0 place-items-center rounded-full hover:bg-rule disabled:cursor-not-allowed disabled:opacity-50";
</script>

<SectionCard title={t("player.nowTitle")} icon={AudioWaveformIcon}>
  {#if player.current}
    <div class="flex flex-col gap-4">
      <div class="flex items-center gap-3">
        <span
          class="grid size-16 shrink-0 place-items-center rounded-chip bg-primary-soft text-primary"
          aria-hidden="true"
        >
          <MusicIcon class="size-7" />
        </span>
        <div class="flex min-w-0 flex-col">
          <p class="truncate text-body font-semibold">{player.current.title}</p>
          <p class="truncate text-caption text-muted-foreground">
            {player.current.artist ?? t("common.unknown")}{player.buffering
              ? ` · ${t("player.buffering")}`
              : ""}
          </p>
        </div>
      </div>

      {#if player.error}
        <Note tone="fail">{player.error}</Note>
      {/if}

      <div class="flex flex-col gap-1">
        <ProgressBar
          {ratio}
          label={t("player.position")}
          valueText={timecode}
          interactive
          onseek={(value) => player.seekRatio(value)}
        />
        <div class="flex items-center justify-between">
          <span class="tabular text-caption text-muted-foreground">
            {formatDuration(player.currentTime)}
          </span>
          <span class="tabular text-caption text-faint-foreground">
            {formatDuration(player.duration)}
          </span>
        </div>
      </div>

      <div class="flex items-center justify-center gap-1">
        <button
          type="button"
          class="{ICON_CLASS} {player.shuffle
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
          class="{ICON_CLASS} text-muted-foreground hover:text-foreground"
          aria-label={t("player.prev")}
          disabled={!player.hasPrev}
          onclick={() => player.prev()}
        >
          <SkipBackIcon class="size-4" aria-hidden="true" />
        </button>

        <button
          type="button"
          class="ui-transition mx-1 grid size-10 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98]"
          aria-label={player.playing ? t("player.pause") : t("player.play")}
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
          class="{ICON_CLASS} text-muted-foreground hover:text-foreground"
          aria-label={t("player.next")}
          disabled={!player.hasNext}
          onclick={() => player.next()}
        >
          <SkipForwardIcon class="size-4" aria-hidden="true" />
        </button>

        <button
          type="button"
          class="{ICON_CLASS} {player.repeat
            ? 'text-primary'
            : 'text-muted-foreground hover:text-foreground'}"
          aria-label={t("player.repeat")}
          aria-pressed={player.repeat}
          onclick={() => (player.repeat = !player.repeat)}
        >
          <RepeatIcon class="size-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  {:else}
    <div class="flex flex-col items-center gap-2 py-2 text-center">
      <MusicIcon class="size-8 text-faint-foreground" aria-hidden="true" />
      <p class="text-body text-muted-foreground">{t("player.idle")}</p>
      <p class="max-w-[32ch] text-caption text-faint-foreground">{t("player.idleHint")}</p>
    </div>
  {/if}
</SectionCard>
