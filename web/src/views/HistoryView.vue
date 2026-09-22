<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api/client";
import { formatDate, formatDuration, formatSize } from "@/utils/format";
import { usePlayerStore, type Track } from "@/stores/player";

interface HistoryRow {
  id: number;
  title: string | null;
  artist: string | null;
  duration_sec: number | null;
  file_size: number | null;
  save_path: string | null;
  status: string;
  created_at: string;
}

const { t } = useI18n();
const rows = ref<HistoryRow[]>([]);
const copiedId = ref<number | null>(null);

async function load() {
  rows.value = await api.get<HistoryRow[]>("/api/history");
}

function playFrom(index: number) {
  const tracks: Track[] = rows.value
    .filter((r) => r.save_path)
    .map((r) => ({
      title: r.title ?? "",
      artist: r.artist,
      streamUrl: `/api/history/${r.id}/stream`,
    }));
  if (tracks.length) usePlayerStore().play(tracks, index);
}

async function copyPath(row: HistoryRow) {
  if (!row.save_path) return;
  await navigator.clipboard.writeText(row.save_path);
  copiedId.value = row.id;
}

onMounted(load);
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("history.title") }}</h1>

    <p v-if="!rows.length" class="text-[13px] text-mid">{{ t("history.empty") }}</p>

    <ul class="divide-y divide-hi/10">
      <li
        v-for="(row, i) in rows"
        :key="row.id"
        class="flex h-14 items-center gap-3 py-2"
      >
        <div class="min-w-0 flex-1">
          <div class="truncate text-[13px] font-medium text-hi">{{ row.title }}</div>
          <div class="truncate text-[12px] text-mid">
            {{ row.artist ?? "" }}
            <template v-if="row.save_path"> | {{ row.save_path }}</template>
          </div>
        </div>
        <span class="hidden font-mono text-[12px] text-mid sm:inline">
          {{ formatDuration(row.duration_sec) }}
        </span>
        <span class="hidden font-mono text-[12px] text-mid lg:inline">
          {{ formatSize(row.file_size) }}
        </span>
        <span class="hidden text-[12px] text-mid md:inline">{{ formatDate(row.created_at) }}</span>
        <div class="flex gap-1.5">
          <button
            v-if="row.save_path"
            class="rounded px-2 py-1 text-[12px] text-accent hover:bg-raise"
            @click="playFrom(i)"
          >
            {{ t("history.play") }}
          </button>
          <button
            v-if="row.save_path"
            class="rounded px-2 py-1 text-[12px] text-hi hover:bg-raise"
            @click="copyPath(row)"
          >
            {{ copiedId === row.id ? "✓" : t("history.copyPath") }}
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>
