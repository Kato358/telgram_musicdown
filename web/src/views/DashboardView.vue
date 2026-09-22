<script setup lang="ts">
import { useI18n } from "vue-i18n";

const { t } = useI18n();
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("dashboard.title") }}</h1>
    <p class="text-[13px] text-mid">{{ t("dashboard.empty") }}</p>
  </div>
</template>
