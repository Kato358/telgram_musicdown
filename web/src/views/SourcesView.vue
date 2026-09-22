<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api/client";

interface SourceRow {
  id: number;
  telegram_chat_id: number;
  username: string | null;
  title: string;
  type: string;
  enabled: boolean;
  auto_sync: boolean;
}

const { t } = useI18n();
const sources = ref<SourceRow[]>([]);
const link = ref("");
const adding = ref(false);
const error = ref("");

async function load() {
  sources.value = await api.get<SourceRow[]>("/api/sources");
}

async function add() {
  adding.value = true;
  error.value = "";
  try {
    await api.post("/api/sources", { link: link.value });
    link.value = "";
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : t("sources.unreachable");
  } finally {
    adding.value = false;
  }
}

async function toggle(row: SourceRow) {
  await api.put(`/api/sources/${row.id}`, { enabled: !row.enabled });
  await load();
}

async function remove(row: SourceRow) {
  await api.delete(`/api/sources/${row.id}`);
  await load();
}

onMounted(load);
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("sources.title") }}</h1>

    <form class="mb-4 flex gap-2" @submit.prevent="add">
      <input
        v-model="link"
        type="text"
        :placeholder="t('sources.linkPlaceholder')"
        class="min-w-0 flex-1 rounded bg-inset px-3 py-2 text-[13px] text-hi placeholder:text-mid/70"
        :aria-label="t('sources.add')"
      />
      <button type="submit" class="rounded bg-accent px-4 py-2 text-[13px] font-medium text-base" :disabled="adding">
        {{ t("sources.add") }}
      </button>
    </form>
    <p v-if="error" class="mb-3 text-[13px] text-fail">{{ error }}</p>

    <p v-if="!sources.length" class="text-[13px] text-mid">{{ t("sources.empty") }}</p>

    <ul class="divide-y divide-hi/10">
      <li v-for="row in sources" :key="row.id" class="flex h-12 items-center gap-3 py-2">
        <span
          class="inline-block h-2 w-2 shrink-0 rounded-full"
          :class="row.enabled ? 'bg-signal' : 'bg-mid'"
          aria-hidden="true"
        />
        <div class="min-w-0 flex-1">
          <div class="truncate text-[13px] text-hi">{{ row.title }}</div>
          <div v-if="row.username" class="truncate text-[12px] text-mid">@{{ row.username }}</div>
        </div>
        <button class="rounded px-2 py-1 text-[12px] text-hi hover:bg-raise" @click="toggle(row)">
          {{ row.enabled ? "停用" : "启用" }}
        </button>
        <button class="rounded px-2 py-1 text-[12px] text-fail hover:bg-raise" @click="remove(row)">
          删除
        </button>
      </li>
    </ul>
  </div>
</template>
