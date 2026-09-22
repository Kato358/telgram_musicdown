<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api/client";
import { useEventsStore } from "@/stores/events";

interface TaskRow {
  id: number;
  type: string;
  status: string;
  progress_bytes: number;
  total_bytes: number | null;
  retry_count: number;
  error: string | null;
}

const { t } = useI18n();
const tasks = ref<TaskRow[]>([]);
const events = useEventsStore();

async function load() {
  tasks.value = await api.get<TaskRow[]>("/api/downloads");
}

async function act(id: number, action: "cancel" | "pause" | "resume") {
  await api.post(`/api/downloads/${id}/${action}`);
  await load();
}

async function retryFailed() {
  await api.post("/api/downloads/retry-failed");
  await load();
}

onMounted(() => {
  load();
  // task.status 事件到达即刷新（SDD §1.4）
  const prevDispatch = events.dispatch.bind(events);
  events.dispatch = (type, payload) => {
    prevDispatch(type, payload);
    if (type === "task.status") load();
  };
});

function statusColor(status: string): string {
  if (status === "success" || status === "skipped") return "bg-signal";
  if (status === "failed") return "bg-fail";
  if (status === "paused") return "bg-warn";
  if (status === "downloading") return "bg-accent";
  return "bg-mid";
}
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <h1 class="text-[17px] font-semibold text-hi">{{ t("tasks.title") }}</h1>
      <button class="rounded px-3 py-1.5 text-[13px] text-accent hover:bg-raise" @click="retryFailed">
        {{ t("tasks.retryFailed") }}
      </button>
    </div>

    <p v-if="!tasks.length" class="text-[13px] text-mid">{{ t("tasks.empty") }}</p>

    <!-- 任务列表行：状态色点 + 文字（设计规范 §5：禁徽章底色块） -->
    <ul class="divide-y divide-hi/10">
      <li v-for="task in tasks" :key="task.id" class="py-2.5">
        <div class="flex h-10 items-center gap-3">
          <span
            class="inline-block h-2 w-2 shrink-0 rounded-full"
            :class="statusColor(task.status)"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <div class="truncate text-[13px] text-hi">
              #{{ task.id }} {{ t(`tasks.status.${task.status}`) }}
              <template v-if="task.error"> | {{ task.error }}</template>
            </div>
            <div
              v-if="task.status === 'downloading' && task.total_bytes"
              class="mt-1 h-0.5 w-full bg-hi/10"
            >
              <div
                class="progress-fill h-full bg-accent"
                :style="{ width: `${Math.min(100, (task.progress_bytes / task.total_bytes) * 100)}%` }"
              />
            </div>
          </div>
          <div class="flex gap-1.5">
            <button
              v-if="task.status === 'downloading'"
              class="rounded px-2 py-1 text-[12px] text-hi hover:bg-raise"
              @click="act(task.id, 'pause')"
            >
              {{ t("tasks.pause") }}
            </button>
            <button
              v-if="task.status === 'paused'"
              class="rounded px-2 py-1 text-[12px] text-hi hover:bg-raise"
              @click="act(task.id, 'resume')"
            >
              {{ t("tasks.resume") }}
            </button>
            <button
              v-if="['queued', 'downloading', 'paused'].includes(task.status)"
              class="rounded px-2 py-1 text-[12px] text-fail hover:bg-raise"
              @click="act(task.id, 'cancel')"
            >
              {{ t("tasks.cancel") }}
            </button>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>
