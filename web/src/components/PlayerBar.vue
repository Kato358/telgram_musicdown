<script setup lang="ts">
import { ref, watch } from "vue";
import { usePlayerStore } from "@/stores/player";
import { formatDuration } from "@/utils/format";

const player = usePlayerStore();
const audioRef = ref<HTMLAudioElement | null>(null);
const src = ref("");

// 切歌：先 pause() 旧实例再换 src（SDD §5.4）
watch(
  () => [player.current?.streamUrl, player.playing] as const,
  ([url, playing]) => {
    const el = audioRef.value;
    if (!el) return;
    if (url && url !== src.value) {
      el.pause();
      src.value = url;
      el.src = url;
    }
    if (playing) {
      el.play().catch(() => {
        player.pause();
      });
    } else {
      el.pause();
    }
  },
  { flush: "post" },
);
</script>

<template>
  <!-- 播放条：唯一强调色浓用的地方（设计规范 §1：音响面板显示区） -->
  <footer class="flex h-16 shrink-0 items-center gap-4 bg-inset px-4">
    <audio ref="audioRef" :src="src" @timeupdate="player.setTime(audioRef?.currentTime ?? 0)" @loadedmetadata="player.setDuration(audioRef?.duration ?? 0)" @ended="player.next()" />
    <template v-if="player.current">
      <div class="min-w-0 flex-1">
        <div class="truncate text-[13px] font-medium text-hi">{{ player.current.title }}</div>
        <div class="truncate text-[12px] text-mid">{{ player.current.artist ?? "" }}</div>
      </div>
      <div class="flex items-center gap-2">
        <button
          class="rounded px-2 py-1 text-hi hover:bg-raise disabled:opacity-40"
          :disabled="!player.hasPrev"
          aria-label="上一首"
          @click="player.prev()"
        >
          ⏮
        </button>
        <button
          class="rounded-full bg-accent px-3 py-1.5 text-[13px] font-medium text-base"
          :aria-label="player.playing ? '暂停' : '播放'"
          @click="player.playing ? player.pause() : player.resume()"
        >
          {{ player.playing ? "⏸" : "▶" }}
        </button>
        <button
          class="rounded px-2 py-1 text-hi hover:bg-raise disabled:opacity-40"
          :disabled="!player.hasNext"
          aria-label="下一首"
          @click="player.next()"
        >
          ⏭
        </button>
      </div>
      <span class="hidden font-mono text-[12px] text-mid sm:inline">
        {{ formatDuration(player.currentTime) }} / {{ formatDuration(player.duration) }}
      </span>
      <input
        type="range"
        class="hidden w-40 accent-[var(--accent)] sm:block"
        :value="player.currentTime"
        :max="player.duration || 0"
        step="0.1"
        aria-label="进度"
        @input="audioRef && (audioRef.currentTime = Number(($event.target as HTMLInputElement).value))"
      />
    </template>
    <div v-else class="flex-1 text-[13px] text-mid">未在播放</div>
  </footer>
</template>
