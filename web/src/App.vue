<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();

/** 未完成初始化（未保存凭据）时强制进入 /setup 向导。 */
const setupChecked = ref(false);
async function checkSetup() {
  try {
    const s = await api.get<{ complete: boolean }>("/api/setup/status");
    if (!s.complete && route.name !== "setup") await router.replace("/setup");
  } catch {
    // 状态接口失败不阻塞浏览
  }
  setupChecked.value = true;
}
watch(() => route.name, checkSetup, { immediate: true });
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <!-- 侧栏 ≥1200px；<1200 收汉堡 -->
    <aside
      class="hidden w-56 shrink-0 flex-col bg-raise px-3 py-4 md:flex"
      aria-label="导航"
    >
      <div class="mb-6 px-2 text-[15px] font-semibold text-hi">
        Telegram 音乐库
      </div>
      <nav class="flex flex-col gap-0.5">
        <RouterLink
          v-for="item in ['dashboard', 'search', 'tasks', 'history', 'sources', 'settings', 'logs']"
          :key="item"
          :to="`/${item}`"
          class="rounded px-3 py-2 text-hi/90 hover:bg-inset"
          :class="route.name === item ? 'bg-inset text-accent' : ''"
        >
          {{ t(`nav.${item}`) }}
        </RouterLink>
      </nav>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <!-- 顶栏：连接状态 + 全局速度 -->
      <header class="flex h-12 shrink-0 items-center gap-4 border-b border-hi/10 px-4">
        <span class="text-[13px] text-mid">
          <span
            class="mr-1.5 inline-block h-2 w-2 rounded-full align-middle"
            :class="connected ? 'bg-signal' : 'bg-fail'"
          />
          {{ connected ? "已连接" : "未连接" }}
        </span>
      </header>

      <!-- 内容区：左对齐，<80ch -->
      <main class="min-w-0 flex-1 overflow-y-auto px-6 py-5">
        <div class="mx-auto max-w-3xl">
          <RouterView v-slot="{ Component }">
            <Transition name="page" mode="out-in">
              <component :is="Component" />
            </Transition>
          </RouterView>
        </div>
      </main>

      <!-- PlayerBar 全局常驻（SDD §5.2） -->
      <PlayerBar />
    </div>
  </div>
</template>
<script lang="ts">
import { defineComponent } from "vue";
import { useEventsStore } from "@/stores/events";
import PlayerBar from "@/components/PlayerBar.vue";

export default defineComponent({
  name: "AppShell",
  components: { PlayerBar },
  computed: {
    connected(): boolean {
      return useEventsStore().connected;
    },
  },
  mounted() {
    useEventsStore().connect();
  },
});
</script>
