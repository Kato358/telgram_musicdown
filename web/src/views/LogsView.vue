<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useEventsStore } from "@/stores/events";

const { t } = useI18n();
const events = useEventsStore();
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("nav.logs") }}</h1>
    <p v-if="!events.errors.length" class="text-[13px] text-mid">没有错误记录。</p>
    <ul v-else class="divide-y divide-hi/10">
      <li v-for="(err, i) in events.errors" :key="i" class="py-2 font-mono text-[12px] text-fail">
        {{ err.message }}
      </li>
    </ul>
  </div>
</template>
