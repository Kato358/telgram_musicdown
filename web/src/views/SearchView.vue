<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api/client";
import { formatDuration, formatSize } from "@/utils/format";
import { usePlayerStore, type Track } from "@/stores/player";

interface SearchResult {
  chat_id: number;
  message_id: number;
  title: string | null;
  artist: string | null;
  duration_sec: number | null;
  file_size: number | null;
  ext: string | null;
  channel_title: string | null;
  message_date: string | null;
  caption: string | null;
}

const { t } = useI18n();
const q = ref("");
const results = ref<SearchResult[]>([]);
const loading = ref(false);
const error = ref("");

async function search() {
  loading.value = true;
  error.value = "";
  try {
    const resp = await api.post<{ results: SearchResult[]; meta: Record<string, unknown> }>(
      "/api/search",
      { q: q.value },
    );
    results.value = resp.results;
  } catch (e) {
    error.value = e instanceof Error ? e.message : t("common.error");
  } finally {
    loading.value = false;
  }
}

async function download(item: SearchResult) {
  await api.post("/api/downloads", {
    message_refs: [{ chat_id: item.chat_id, message_id: item.message_id }],
  });
}

/** 试听：先请求 preview 缓存（FR-PLAY-02），再从本地流播放。 */
async function preview(item: SearchResult) {
  await api.post("/api/preview", {
    message_refs: [{ chat_id: item.chat_id, message_id: item.message_id }],
  });
  playFrom(results.value.indexOf(item));
}

function playFrom(index: number) {
  const tracks: Track[] = results.value.map((r) => ({
    title: r.title ?? "",
    artist: r.artist,
    streamUrl: `/api/preview/${r.message_id}/stream`,
  }));
  usePlayerStore().play(tracks, index);
}
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("search.title") }}</h1>
    <form class="mb-4 flex gap-2" @submit.prevent="search">
      <input
        v-model="q"
        type="search"
        :placeholder="t('search.placeholder')"
        class="min-w-0 flex-1 rounded bg-inset px-3 py-2 text-[13px] text-hi placeholder:text-mid/70"
        :aria-label="t('search.title')"
      />
      <button
        type="submit"
        class="rounded bg-accent px-4 py-2 text-[13px] font-medium text-base"
        :disabled="loading"
      >
        {{ t("search.search") }}
      </button>
    </form>

    <p v-if="error" class="mb-3 text-[13px] text-fail">{{ error }}</p>
    <p v-if="!results.length && !loading && !error" class="text-[13px] text-mid">
      {{ t("search.noResults") }}
    </p>

    <!-- 结果列表行（设计规范 §4：列表行式，非卡片） -->
    <ul class="divide-y divide-hi/10">
      <li
        v-for="item in results"
        :key="`${item.chat_id}-${item.message_id}`"
        class="flex h-14 items-center gap-3 py-2"
      >
        <div class="min-w-0 flex-1">
          <div class="truncate text-[13px] font-medium text-hi">{{ item.title }}</div>
          <div class="truncate text-[12px] text-mid">
            {{ item.artist ?? "" }}
            <template v-if="item.channel_title"> | {{ item.channel_title }}</template>
          </div>
        </div>
        <span class="font-mono text-[12px] text-mid">{{ formatDuration(item.duration_sec) }}</span>
        <span class="hidden font-mono text-[12px] text-mid sm:inline">{{ formatSize(item.file_size) }}</span>
        <div class="flex gap-1.5">
          <button class="rounded px-2 py-1 text-[12px] text-accent hover:bg-raise" @click="preview(item)">
            {{ t("search.preview") }}
          </button>
          <button
            class="rounded bg-accent/15 px-2.5 py-1 text-[12px] text-accent hover:bg-accent/25"
            @click="download(item)"
          >
            {{ t("search.download") }}
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>
