<script lang="ts">
  /** 设置：落盘命名、下载并发与试听缓存、界面偏好（FR-SET-01/02）。
   *
   * 命名模板改动即时预览（防抖 200ms，POST /api/settings/preview-path），但只有「保存更改」写库；
   * 缓存上限在库里是字节字符串，界面按 MB 编辑，保存时换算。界面两项只存本机浏览器。
   */
  import { onMount } from "svelte";
  import { api, errorText } from "$lib/api/client";
  import { i18n, LOCALES, t } from "$lib/i18n/index.svelte";
  import { theme, THEME_OPTIONS } from "$lib/stores/theme.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
  } from "$lib/components/ui/select";
  import Field from "$lib/components/app/Field.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";


  const BYTES_PER_MB = 1024 * 1024;
  const DEFAULT_CACHE_MB = 512;

  /** 后端缺键时的文档默认值（settings 表只存已写过的键）。 */
  const DEFAULTS: Record<string, string> = {
    dir_template: "{artist}/{album}",
    file_template: "{track:02d} {title}",
    date_format: "%Y-%m",
    max_download_task: "3",
    preview_cache_max_bytes: String(DEFAULT_CACHE_MB * BYTES_PER_MB),
  };

  /** 触发器关闭时 bits-ui 不渲染选项，标签只能由 items 提供。 */
  const appearanceItems = $derived(
    THEME_OPTIONS.map((item) => ({ value: item.value, label: t(item.label) })),
  );

  let dirTemplate = $state("");
  let fileTemplate = $state("");
  let dateFormat = $state("");
  let maxTasks = $state("");
  let cacheMb = $state("");

  let loaded = $state(false);
  let loadError = $state("");

  let saving = $state(false);
  let saveError = $state("");
  /** 已保存值的指纹：编辑后与当前指纹不等即「有未保存改动」，成功提示随之收起。 */
  let savedKey = $state<string | null>(null);

  let preview = $state("");
  let previewError = $state("");

  /** 缺键或空值都退回默认，避免把空模板写回服务端。 */
  function pick(values: Record<string, string>, key: string): string {
    const value = values[key];
    return typeof value === "string" && value.length > 0 ? value : DEFAULTS[key];
  }

  function toMb(bytes: string): string {
    const value = Number(bytes);
    const megabytes =
      Number.isFinite(value) && value > 0 ? Math.round(value / BYTES_PER_MB) : DEFAULT_CACHE_MB;
    return String(megabytes);
  }

  function toBytes(megabytes: string): number {
    const value = Number(megabytes);
    return Number.isFinite(value) && value > 0
      ? Math.round(value * BYTES_PER_MB)
      : DEFAULT_CACHE_MB * BYTES_PER_MB;
  }

  function apply(values: Record<string, string>) {
    dirTemplate = pick(values, "dir_template");
    fileTemplate = pick(values, "file_template");
    dateFormat = pick(values, "date_format");
    maxTasks = pick(values, "max_download_task");
    cacheMb = toMb(pick(values, "preview_cache_max_bytes"));
  }

  function fingerprint(): string {
    return [dirTemplate, fileTemplate, dateFormat, maxTasks, cacheMb].join("\u0000");
  }

  const dirty = $derived(savedKey === null || fingerprint() !== savedKey);

  async function load() {
    try {
      apply(await api.get<Record<string, string>>("/api/settings"));
      loadError = "";
      loaded = true;
    } catch (err) {
      loadError = errorText(err, t("common.error"));
    }
  }

  async function runPreview(dir: string, file: string) {
    try {
      const resp = await api.post<{ path: string }>("/api/settings/preview-path", {
        dir_template: dir,
        file_template: file,
      });
      preview = resp.path;
      previewError = "";
    } catch (err) {
      preview = "";
      previewError = errorText(err, t("common.error"));
    }
  }

  /** 模板改动只影响预览，写库要等「保存更改」。 */
  $effect(() => {
    const dir = dirTemplate;
    const file = fileTemplate;
    if (!loaded) return;
    const timer = setTimeout(() => void runPreview(dir, file), 200);
    return () => clearTimeout(timer);
  });

  async function save() {
    if (saving) return;
    saving = true;
    saveError = "";
    try {
      const values: Record<string, string> = {
        dir_template: dirTemplate,
        file_template: fileTemplate,
        date_format: dateFormat,
        max_download_task: maxTasks,
        preview_cache_max_bytes: String(toBytes(cacheMb)),
      };
      apply(await api.put<Record<string, string>>("/api/settings", { values }));
      savedKey = fingerprint();
    } catch (err) {
      saveError = errorText(err, t("common.error"));
    } finally {
      saving = false;
    }
  }

  function changeLocale(value: string) {
    const next = LOCALES.find((item) => item.value === value);
    if (next) i18n.setLocale(next.value);
  }

  function changeAppearance(value: string) {
    const next = THEME_OPTIONS.find((item) => item.value === value);
    if (next) theme.set(next.value);
  }

  onMount(() => {
    void load();
  });
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("settings.title")} lede={t("settings.lede")} />

  {#if loadError}
    <Note tone="fail">{loadError}</Note>
  {/if}

  <section class="flex flex-col gap-4 border-t border-rule pt-4">
    <h2 class="text-body font-medium">{t("settings.pathSection")}</h2>
    <p class="text-small text-muted-foreground">{t("settings.pathHint")}</p>

    <Field label={t("settings.dirTemplate")} for="setting-dir-template">
      <Input id="setting-dir-template" class="tabular" bind:value={dirTemplate} />
    </Field>

    <Field label={t("settings.fileTemplate")} for="setting-file-template">
      <Input id="setting-file-template" class="tabular" bind:value={fileTemplate} />
    </Field>

    <Field label={t("settings.dateFormat")} for="setting-date-format">
      <Input id="setting-date-format" class="tabular w-40" bind:value={dateFormat} />
    </Field>

    <div class="flex flex-col gap-1.5">
      <span class="text-micro text-muted-foreground">{t("settings.preview")}</span>
      {#if preview}
        <code class="tabular rounded-md bg-muted px-3 py-2 text-body break-all">{preview}</code>
      {:else if !loadError}
        <p class="tabular rounded-md bg-muted px-3 py-2 text-body break-all text-muted-foreground">
          {t("common.loading")}
        </p>
      {/if}
    </div>

    {#if previewError}
      <Note tone="fail">{previewError}</Note>
    {/if}

    <div class="flex items-center gap-3">
      <Button disabled={saving || !loaded} onclick={() => void save()}>
        {saving ? t("settings.saving") : t("settings.save")}
      </Button>
      {#if savedKey !== null && !dirty}
        <Note tone="done">{t("settings.saved")}</Note>
      {/if}
    </div>

    {#if saveError}
      <Note tone="fail">{saveError}</Note>
    {/if}
  </section>

  <section class="flex flex-col gap-4 border-t border-rule pt-4">
    <h2 class="text-body font-medium">{t("settings.downloadSection")}</h2>

    <Field label={t("settings.maxTasks")} for="setting-max-tasks" hint={t("settings.maxTasksHint")}>
      <Input
        id="setting-max-tasks"
        type="number"
        min="1"
        class="tabular w-32"
        bind:value={maxTasks}
      />
    </Field>

    <Field
      label={t("settings.previewCache")}
      for="setting-preview-cache"
      hint={t("settings.previewCacheHint")}
    >
      <Input
        id="setting-preview-cache"
        type="number"
        min="64"
        class="tabular w-32"
        bind:value={cacheMb}
      />
    </Field>

    <p class="text-micro text-lamp-wait">{t("settings.restartHint")}</p>
  </section>

  <section class="flex flex-col gap-4 border-t border-rule pt-4">
    <h2 class="text-body font-medium">{t("settings.interfaceSection")}</h2>
    <p class="text-small text-muted-foreground">{t("settings.interfaceHint")}</p>

    <Field label={t("settings.language")} for="setting-language">
      <Select type="single" value={i18n.locale} items={LOCALES} onValueChange={changeLocale}>
        <SelectTrigger id="setting-language" class="w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {#each LOCALES as locale (locale.value)}
            <SelectItem value={locale.value}>{locale.label}</SelectItem>
          {/each}
        </SelectContent>
      </Select>
    </Field>

    <Field label={t("settings.theme")} for="setting-appearance">
      <Select
        type="single"
        value={theme.preference}
        items={appearanceItems}
        onValueChange={changeAppearance}
      >
        <SelectTrigger id="setting-appearance" class="w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {#each THEME_OPTIONS as option (option.value)}
            <SelectItem value={option.value}>{t(option.label)}</SelectItem>
          {/each}
        </SelectContent>
      </Select>
    </Field>
  </section>
</div>
