<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api/client";

const { t } = useI18n();
const dirTemplate = ref("{artist}/{album}");
const fileTemplate = ref("{track:02d} {title}");
const previewPath = ref("");
const saved = ref(false);

async function load() {
  const all = await api.get<Record<string, string>>("/api/settings");
  if (all.dir_template) dirTemplate.value = all.dir_template;
  if (all.file_template) fileTemplate.value = all.file_template;
}

async function save() {
  await api.put("/api/settings", {
    values: { dir_template: dirTemplate.value, file_template: fileTemplate.value },
  });
  saved.value = true;
}

watch([dirTemplate, fileTemplate], async () => {
  const resp = await api.post<{ path: string }>("/api/settings/preview-path", {
    dir_template: dirTemplate.value,
    file_template: fileTemplate.value,
  });
  previewPath.value = resp.path;
  saved.value = false;
});

onMounted(async () => {
  await load();
  const resp = await api.post<{ path: string }>("/api/settings/preview-path", {
    dir_template: dirTemplate.value,
    file_template: fileTemplate.value,
  });
  previewPath.value = resp.path;
});
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("settings.title") }}</h1>

    <!-- 模板编辑：左编辑右实时预览（SDD §5.2 TemplateEditor） -->
    <div class="grid gap-6 md:grid-cols-2">
      <div class="flex flex-col gap-4">
        <label class="flex flex-col gap-1.5">
          <span class="text-[13px] text-mid">{{ t("settings.dirTemplate") }}</span>
          <input
            v-model="dirTemplate"
            type="text"
            class="rounded bg-inset px-3 py-2 font-mono text-[13px] text-hi"
          />
        </label>
        <label class="flex flex-col gap-1.5">
          <span class="text-[13px] text-mid">{{ t("settings.fileTemplate") }}</span>
          <input
            v-model="fileTemplate"
            type="text"
            class="rounded bg-inset px-3 py-2 font-mono text-[13px] text-hi"
          />
        </label>
        <button class="self-start rounded bg-accent px-4 py-2 text-[13px] font-medium text-base" @click="save">
          {{ t("settings.save") }}
        </button>
        <span v-if="saved" class="text-[13px] text-signal">{{ t("settings.saved") }}</span>
      </div>

      <div class="flex flex-col gap-1.5">
        <span class="text-[13px] text-mid">{{ t("settings.previewPath") }}</span>
        <code class="break-all rounded bg-inset px-3 py-2 font-mono text-[12px] text-accent">
          {{ previewPath }}
        </code>
      </div>
    </div>
  </div>
</template>
