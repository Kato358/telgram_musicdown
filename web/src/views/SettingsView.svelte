<script lang="ts">
  /** 设置：账号与连接（Telegram 账号 / Bot / 代理）、落盘命名、下载并发与试听缓存、界面偏好
   * （FR-SET-01/02、FR-AUTH-02/03/04、FR-OPS-02）。
   *
   * 桌面端（xl+）1:1 双栏：左栏「落盘命名 + 下载并发 + 界面」，右栏「账号 / Bot / 代理 / 重置」；
   * 窄屏退回单栏，区块顺序不变。
   *
   * 命名模板改动即时预览（防抖 200ms，POST /api/settings/preview-path），但只有「保存更改」写库；
   * 预览用后端内置的真实歌曲示例（响应里带落盘根 root，前端淡显区分根与渲染段）。
   * 模板字段定义来自 GET /api/settings/template-fields（字段名 + 示例值，说明文案在 i18n），
   * 界面只列常用白名单 COMMON_FIELDS，点按字段插入到最后聚焦的模板框。缓存上限在库里是字节字符串，界面按 MB 编辑，保存时换算。
   * 界面两项只存本机浏览器。
   *
   * 密钥类改动（bot_token / 代理）走 `POST /api/setup/secrets` 写 config.yaml：留空 = 不改动，
   * 用户名密码不回显（NFR-02）；已连上的客户端由返回的 restart_required 决定提示重启还是即时生效。
   * 「退出登录」与「重新执行初始化」都会清掉登录态，故做完立刻跳回向导（放行判据含登录）。
   *
   * 缓存占用（试听 + 封面）与上限同屏：占用取 `GET /api/settings/cache`（磁盘实际字节），
   * 「清理缓存」走 `POST /api/settings/cache/clear`，两者只碰缓存目录，不动曲库与历史。
   */
  import { onMount } from "svelte";
  import BotIcon from "@lucide/svelte/icons/bot";
  import CircleUserIcon from "@lucide/svelte/icons/circle-user";
  import CloudIcon from "@lucide/svelte/icons/cloud";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import FolderTreeIcon from "@lucide/svelte/icons/folder-tree";
  import NetworkIcon from "@lucide/svelte/icons/network";
  import PaletteIcon from "@lucide/svelte/icons/palette";
  import RotateCcwIcon from "@lucide/svelte/icons/rotate-ccw";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { api, errorText } from "$lib/api/client";
  import type {
    CacheStats,
    QualityOption,
    QualityTier,
    QualitiesResponse,
    SetupProxyInput,
  } from "$lib/api/types";
  import { formatSize } from "$lib/format";
  import { i18n, LOCALES, t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { BOT_TOKEN_RE, MAX_PORT, PORT_RE } from "$lib/secrets";
  import { session } from "$lib/stores/session.svelte";
  import { theme, THEME_OPTIONS } from "$lib/stores/theme.svelte";
  import type { Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
  } from "$lib/components/ui/dialog";
  import { Input } from "$lib/components/ui/input";
  import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
  } from "$lib/components/ui/select";
  import Field from "$lib/components/app/Field.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import SectionCard from "$lib/components/app/SectionCard.svelte";

  type Feedback = { tone: Tone; text: string };

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

  // ---- 缓存占用（FR-PLAY-02）----

  let cacheStats = $state<CacheStats | null>(null);
  let cacheNote = $state<Feedback | null>(null);
  let clearing = $state(false);

  /** 占用文案：空缓存直说「暂无」，有货就报「占用 / 上限 · 各有几份」。 */
  const cacheDetail = $derived.by(() => {
    const stats = cacheStats;
    if (!stats) return null;
    if (stats.total_bytes === 0) return t("settings.cacheEmpty");
    return t("settings.cacheDetail", {
      used: formatSize(stats.total_bytes),
      max: formatSize(stats.max_bytes),
      previews: stats.preview_count,
      covers: stats.cover_count,
    });
  });

  let loaded = $state(false);
  let loadError = $state("");

  let saving = $state(false);
  let saveError = $state("");
  /** 已保存值的指纹：编辑后与当前指纹不等即「有未保存改动」，成功提示随之收起。 */
  let savedKey = $state<string | null>(null);

  // ---- 搜索模式（FR-SEARCH-01）：点按即写库，不走「保存更改」----

  let savingMode = $state(false);
  let modeNote = $state<Feedback | null>(null);

  /** 两种模式的文案键（标题 + 说明 + 卡片下方一行提示）集中一处，选项与提示复用。 */
  const SEARCH_MODES = [
    {
      value: "sources",
      label: "settings.searchModeSources",
      desc: "settings.searchModeSourcesDesc",
      note: "settings.searchModeSourcesNote",
    },
    {
      value: "global",
      label: "settings.searchModeGlobal",
      desc: "settings.searchModeGlobalDesc",
      note: "settings.searchModeGlobalNote",
    },
  ] as const;

  /** 当前生效的选项：状态缺省按 sources 兜底（与 store、后端一致）。 */
  const currentMode = $derived(
    SEARCH_MODES.find((item) => item.value === session.searchMode) ?? SEARCH_MODES[0],
  );

  let preview = $state("");
  let previewRoot = $state("");
  let previewError = $state("");

  // ---- 模板字段定义（FR-NAME-02：字段 + 示例值来自后端，说明文案在 i18n）----

  type TemplateFieldDoc = { field: string; example: string };

  let fieldDocs = $state<TemplateFieldDoc[]>([]);
  let fieldSample = $state<{ artist: string | null; title: string | null } | null>(null);
  /** 点按字段插入的目标：最后聚焦的模板框（默认文件名模板）；mousedown 兜底窗口无焦点时的点击。 */
  let lastTemplate: "dir" | "file" = "file";

  /** 模板字段面板只列出这些常用字段；其余字段模板里仍然可用，只是不再展示。 */
  const COMMON_FIELDS = new Set(["title", "artist", "album", "track", "ext", "date"]);

  /** 字段名 → i18n 说明键（settings.* 下）。 */
  const FIELD_KEY: Record<string, string> = {
    title: "fieldTitle",
    artist: "fieldArtist",
    album: "fieldAlbum",
    track: "fieldTrack",
    ext: "fieldExt",
    duration: "fieldDuration",
    bitrate: "fieldBitrate",
    size: "fieldSize",
    date: "fieldDate",
    year: "fieldYear",
    channel: "fieldChannel",
    channel_id: "fieldChannelId",
    message_id: "fieldMessageId",
    caption: "fieldCaption",
    file_name: "fieldFileName",
    unique_id: "fieldUniqueId",
  };

  /** 过滤器语法（说明走 i18n；示例值直接渲染，不经 t 的插值）。 */
  const TEMPLATE_FILTERS = [
    { syntax: "{field:02d}", key: "settings.filterPad", example: "{track:02d} → 01" },
    {
      syntax: "{field:truncate:N}",
      key: "settings.filterTruncate",
      example: "{title:truncate:24}",
    },
    { syntax: "{field:%Y-%m}", key: "settings.filterDate", example: "{date:%Y-%m} → 2024-05" },
  ];

  function fieldLabel(name: string): string {
    const key = FIELD_KEY[name];
    return key ? t(`settings.${key}`) : name;
  }

  // ---- 账号与连接（FR-AUTH-02/03/04、FR-OPS-02）----

  const me = $derived(session.me);
  const status = $derived(session.setup);

  let accountNote = $state<Feedback | null>(null);
  let logoutOpen = $state(false);
  let loggingOut = $state(false);

  /** 在线源 ChKSz（SDD §2.7）：开关与两档音质走 settings 表，Key 走 config.yaml。 */
  let chkszEnabled = $state(false);
  let chkszDownloadQuality = $state<QualityTier>("hires");
  let chkszPreviewQuality = $state<QualityTier>("320k");
  let chkszKey = $state("");
  let savingChkszKey = $state(false);
  let chkszNote: Feedback | null = $state(null);
  /** 各在线源平台的音质阶梯（服务端发的语义档位表）。 */
  let chkszTiers = $state<QualityOption[]>([]);
  /** bits-ui 的 Select 要 {value,label}；语义档位就是 value。 */
  const chkszTierItems = $derived(chkszTiers.map((o) => ({ value: o.tier, label: o.label })));
  let hasChkszKey = $state(false);
  let botToken = $state("");
  let botNote = $state<Feedback | null>(null);
  let savingBot = $state(false);

  let useProxy = $state(false);
  let proxyScheme = $state("socks5");
  let proxyHost = $state("");
  let proxyPort = $state("1080");
  let proxyUser = $state("");
  let proxyPass = $state("");
  let proxyNote = $state<Feedback | null>(null);
  let savingProxy = $state(false);

  let resetOpen = $state(false);
  let resetting = $state(false);
  let resetError = $state("");

  /** 账号行文案：拿不到显示名时退回用户名，都没有就说未命名（不假装有名字）。 */
  const accountName = $derived(
    me?.display_name || (me?.username ? `@${me.username}` : t("settings.accountUnnamed")),
  );

  /** 代理现状：只有协议/地址/端口（用户名密码不出网，NFR-02）。 */
  const proxySummary = $derived.by(() => {
    const proxy = status?.proxy ?? null;
    if (!proxy?.hostname) return t("settings.proxyDirect");
    return t("settings.proxyCurrent", {
      scheme: proxy.scheme.toUpperCase(),
      host: proxy.hostname,
      port: proxy.port,
    });
  });

  /** 表单初值取服务端状态：代理回填地址端口，用户名密码留空（留空 = 沿用已存值）。 */
  function applyConnectionState() {
    const proxy = status?.proxy ?? null;
    useProxy = Boolean(proxy?.hostname);
    proxyScheme = proxy?.scheme ?? "socks5";
    proxyHost = proxy?.hostname ?? "";
    proxyPort = proxy ? String(proxy.port) : "1080";
    proxyUser = "";
    proxyPass = "";
  }

  async function loadConnection() {
    try {
      await Promise.all([session.loadMe(), session.loadSetup()]);
      applyConnectionState();
    } catch (err) {
      accountNote = { tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  /** 退出去向导：放行判据含登录，清掉登录态后设置页里每个接口都会报未连接。 */
  async function confirmLogout() {
    if (loggingOut) return;
    loggingOut = true;
    accountNote = null;
    try {
      await session.signOut();
      logoutOpen = false;
      navigate(pathOf("setup"));
    } catch (err) {
      logoutOpen = false;
      accountNote = { tone: "fail", text: errorText(err, t("common.error")) };
    } finally {
      loggingOut = false;
    }
  }

  async function saveBot() {
    if (savingBot) return;
    const token = botToken.trim();
    if (token !== "" && !BOT_TOKEN_RE.test(token)) {
      botNote = { tone: "fail", text: t("settings.botTokenInvalid") };
      return;
    }
    if (token === "") return; // 按钮已禁用，留空只是保留现值
    savingBot = true;
    botNote = null;
    try {
      const resp = await api.post<{ restart_required: boolean }>("/api/setup/secrets", {
        bot_token: token,
      });
      botToken = "";
      await loadConnection();
      botNote = {
        tone: "done",
        text: t(resp.restart_required ? "settings.botSavedRestart" : "settings.botSaved"),
      };
    } catch (err) {
      botNote = { tone: "fail", text: errorText(err, t("common.error")) };
    } finally {
      savingBot = false;
    }
  }

  /** 前端只做即时反馈；判据在服务端（同一套规则，见 app/services/setup.py）。 */
  function proxyProblems(): string[] {
    const problems: string[] = [];
    if (!useProxy) return problems;
    if (proxyHost.trim() === "") problems.push(t("settings.proxyHostRequired"));
    const port = Number(proxyPort.trim());
    if (!PORT_RE.test(proxyPort.trim()) || port < 1 || port > MAX_PORT) {
      problems.push(t("settings.proxyPortInvalid"));
    }
    return problems;
  }

  async function saveProxy() {
    if (savingProxy) return;
    const problems = proxyProblems();
    if (problems.length > 0) {
      proxyNote = {
        tone: "fail",
        text: t("settings.proxyInvalid", { items: problems.join("、") }),
      };
      return;
    }
    savingProxy = true;
    proxyNote = null;
    try {
      const proxy: SetupProxyInput | null = useProxy
        ? {
            scheme: proxyScheme,
            hostname: proxyHost.trim(),
            port: Number(proxyPort.trim()),
            ...(proxyUser.trim() ? { username: proxyUser.trim() } : {}),
            ...(proxyPass ? { password: proxyPass } : {}),
          }
        : null;
      const resp = await api.post<{ restart_required: boolean }>("/api/setup/secrets", { proxy });
      await loadConnection();
      proxyNote = {
        tone: "done",
        text: t(
          !useProxy
            ? "settings.proxySavedDirect"
            : resp.restart_required
              ? "settings.proxySavedRestart"
              : "settings.proxySaved",
        ),
      };
    } catch (err) {
      proxyNote = { tone: "fail", text: errorText(err, t("common.error")) };
    } finally {
      savingProxy = false;
    }
  }

  async function confirmReset() {
    if (resetting) return;
    resetting = true;
    resetError = "";
    try {
      await api.post("/api/setup/reset");
      await loadConnection();
      resetOpen = false;
      navigate(pathOf("setup"));
    } catch (err) {
      resetError = errorText(err, t("common.error"));
    } finally {
      resetting = false;
    }
  }

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
    // 目录模板空值合法（空 = 平铺落根目录）：只有缺键（从未保存过）才回退默认
    dirTemplate = values.dir_template !== undefined ? values.dir_template : DEFAULTS.dir_template;
    fileTemplate = pick(values, "file_template");
    dateFormat = pick(values, "date_format");
    maxTasks = pick(values, "max_download_task");
    cacheMb = toMb(pick(values, "preview_cache_max_bytes"));
    // 在线源（SDD §2.7）：开关与两档音质都在 settings 表，热更新不必重启
    chkszEnabled = truthy(values.chksz_enabled);
    chkszDownloadQuality = tierOf(pick(values, "chksz_download_quality"), DEFAULTS.chksz_download_quality);
    chkszPreviewQuality = tierOf(pick(values, "chksz_preview_quality"), DEFAULTS.chksz_preview_quality);
  }

  function fingerprint(): string {
    return [
      dirTemplate,
      fileTemplate,
      dateFormat,
      maxTasks,
      cacheMb,
      chkszEnabled,
      chkszDownloadQuality,
      chkszPreviewQuality,
    ].join("\u0000");
  }

  const dirty = $derived(savedKey === null || fingerprint() !== savedKey);

  async function load() {
    try {
      apply(await api.get<Record<string, string>>("/api/settings"));
      // 阶梯是静态文档，与开关分开取；三家平台共用同一张语义档位表
      const resp = await api.get<QualitiesResponse>("/api/settings/qualities");
      chkszTiers = resp.providers["163"] ?? [];
      hasChkszKey = session.setup?.has_chksz_key ?? false;
      loadError = "";
      loaded = true;
    } catch (err) {
      loadError = errorText(err, t("common.error"));
    }
  }

  function truthy(raw: string | undefined): boolean {
    return raw === "true" || raw === "1" || raw === "on";
  }

  /** 库里存的是语义档位；认不出就落回缺省，别把一个错值塞进下载请求。 */
  function tierOf(raw: string | undefined, fallback: string): QualityTier {
    const known: QualityTier[] = [
    "128k",
    "320k",
    "lossless",
    "hires",
    "master",
    "sky",
    "jyeffect",
  ];
    return known.includes(raw as QualityTier) ? (raw as QualityTier) : (fallback as QualityTier);
  }

  /** Key 单独存 config.yaml（不入库，NFR-02）：留空 = 不改动，服务端不回显明文。 */
  async function saveChkszKey() {
    if (savingChkszKey) return;
    const key = chkszKey.trim();
    if (key === "") return;
    savingChkszKey = true;
    chkszNote = null;
    try {
      await api.post("/api/setup/secrets", { chksz_api_key: key });
      chkszKey = "";
      hasChkszKey = true;
      chkszNote = { tone: "done", text: t("settings.chkszKeySaved") };
    } catch (err) {
      chkszNote = { tone: "fail", text: errorText(err, t("common.error")) };
    } finally {
      savingChkszKey = false;
    }
  }

  /** 占用随页面加载、保存后（上限可能变了）各取一次；失败只提示，不阻塞设置页。 */
  async function loadCacheStats() {
    try {
      cacheStats = await api.get<CacheStats>("/api/settings/cache");
    } catch (err) {
      cacheNote = { tone: "fail", text: errorText(err, t("common.error")) };
    }
  }

  async function clearCache() {
    if (clearing) return;
    clearing = true;
    cacheNote = null;
    const before = cacheStats?.total_bytes ?? 0;
    try {
      cacheStats = await api.post<CacheStats>("/api/settings/cache/clear");
      cacheNote = {
        tone: "done",
        text: t("settings.cacheCleared", { size: formatSize(before) }),
      };
    } catch (err) {
      cacheNote = { tone: "fail", text: errorText(err, t("common.error")) };
    } finally {
      clearing = false;
    }
  }

  async function loadFieldDocs() {
    try {
      const resp = await api.get<{
        fields: TemplateFieldDoc[];
        sample: { artist: string | null; title: string | null };
      }>("/api/settings/template-fields");
      fieldDocs = resp.fields.filter((doc) => COMMON_FIELDS.has(doc.field));
      fieldSample = resp.sample;
    } catch {
      // 字段表加载失败不阻塞设置页，仅少一块文档
    }
  }

  async function runPreview(dir: string, file: string) {
    try {
      const resp = await api.post<{ path: string; root?: string }>("/api/settings/preview-path", {
        dir_template: dir,
        file_template: file,
      });
      preview = resp.path;
      previewRoot = resp.root ?? "";
      previewError = "";
    } catch (err) {
      preview = "";
      previewRoot = "";
      previewError = errorText(err, t("common.error"));
    }
  }

  /** 预览拆成「落盘根（淡显）+ 渲染段」：根来自库里 save_path，模板只决定后半段。 */
  const previewParts = $derived.by(() => {
    if (!preview) return null;
    let root = previewRoot !== "" && preview.startsWith(previewRoot) ? previewRoot : "";
    let rest = preview.slice(root.length);
    if (root !== "" && /^[\\/]/.test(rest)) {
      root += rest[0];
      rest = rest.slice(1);
    }
    return { root, rest };
  });

  /** 点按字段：以空格衔接追加到最后聚焦的模板框，预览随之刷新。 */
  function insertField(name: string) {
    const current = lastTemplate === "dir" ? dirTemplate : fileTemplate;
    const glue = current === "" || /[\s/\\]$/.test(current) ? "" : " ";
    const next = current + glue + `{${name}}`;
    if (lastTemplate === "dir") dirTemplate = next;
    else fileTemplate = next;
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
        chksz_enabled: String(chkszEnabled),
        chksz_download_quality: chkszDownloadQuality,
        chksz_preview_quality: chkszPreviewQuality,
      };
      apply(await api.put<Record<string, string>>("/api/settings", { values }));
      savedKey = fingerprint();
      void loadCacheStats(); // 上限可能刚被改：占用条目的「/ 上限」要跟着走
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

  /** 点按即生效：savingMode 挡住重复点击，成功后提示带上刚选中的模式名。 */
  async function chooseMode(value: string) {
    if (savingMode || value === session.searchMode) return;
    const option = SEARCH_MODES.find((item) => item.value === value);
    if (!option) return;
    savingMode = true;
    modeNote = null;
    try {
      await session.setSearchMode(value);
      modeNote = { tone: "done", text: t("settings.searchModeSaved", { mode: t(option.label) }) };
    } catch (err) {
      modeNote = {
        tone: "fail",
        text: t("settings.searchModeFailed", { reason: errorText(err, t("common.error")) }),
      };
    } finally {
      savingMode = false;
    }
  }

  onMount(() => {
    void load();
    void loadFieldDocs();
    void loadConnection();
    void loadCacheStats();
  });
</script>

<PageHeader title={t("settings.title")} lede={t("settings.lede")} />

{#if loadError}
  <Note tone="fail">{loadError}</Note>
{/if}

<div class="grid items-start gap-4 md:gap-6 xl:grid-cols-2">
  <!-- 桌面端（xl+）1:1 双栏：左栏「搜索模式 + 落盘命名 + 下载 + 界面」，右栏「账号 / Bot / 代理 / 重置」；窄屏退回单栏，顺序不变 -->
  <div class="flex min-w-0 flex-col gap-4 md:gap-6">
    <SectionCard
      title={t("settings.searchModeSection")}
      hint={t("settings.searchModeHint")}
      icon={SearchIcon}
    >
      <div class="flex flex-col gap-4">
        <div class="grid gap-3 sm:grid-cols-2">
          {#each SEARCH_MODES as option (option.value)}
            {@const active = currentMode.value === option.value}
            <button
              type="button"
              class="ui-transition flex flex-col gap-1 rounded-control border p-3 text-left {active
                ? 'border-primary bg-primary-soft'
                : 'border-border hover:bg-rule'}"
              aria-pressed={active}
              onclick={() => void chooseMode(option.value)}
            >
              <span class="text-caption font-semibold {active ? 'text-primary' : ''}">
                {t(option.label)}
              </span>
              <span class="text-caption {active ? 'text-primary' : 'text-muted-foreground'}">
                {t(option.desc)}
              </span>
            </button>
          {/each}
        </div>

        <p class="text-caption text-faint-foreground">{t(currentMode.note)}</p>

        {#if modeNote}
          <Note tone={modeNote.tone}>{modeNote.text}</Note>
        {/if}
      </div>
    </SectionCard>

    <SectionCard
      title={t("settings.pathSection")}
      hint={t("settings.pathHint")}
      icon={FolderTreeIcon}
    >
      <div class="flex flex-col gap-4">
        <div class="grid gap-4 sm:grid-cols-2">
          <Field
            label={t("settings.dirTemplate")}
            for="setting-dir-template"
            hint={t("settings.dirTemplateHint")}
          >
            <Input
              id="setting-dir-template"
              class="tabular"
              bind:value={dirTemplate}
              onfocus={() => (lastTemplate = "dir")}
              onmousedown={() => (lastTemplate = "dir")}
            />
          </Field>

          <Field label={t("settings.fileTemplate")} for="setting-file-template">
            <Input
              id="setting-file-template"
              class="tabular"
              bind:value={fileTemplate}
              onfocus={() => (lastTemplate = "file")}
              onmousedown={() => (lastTemplate = "file")}
            />
          </Field>
        </div>

        <Field label={t("settings.dateFormat")} for="setting-date-format">
          <Input id="setting-date-format" class="tabular w-40" bind:value={dateFormat} />
        </Field>

        <div class="rounded-control border border-border bg-surface-subtle">
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-3 pt-2.5">
            <span class="text-caption font-semibold">{t("settings.templateFieldsTitle")}</span>
            <span class="text-caption text-faint-foreground"
              >{t("settings.templateFieldsHint")}</span
            >
          </div>
          {#if fieldDocs.length > 0}
            <div class="grid gap-x-6 px-2 py-1.5">
              {#each fieldDocs as doc (doc.field)}
                <button
                  type="button"
                  class="ui-transition flex min-w-0 items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-rule"
                  onclick={() => insertField(doc.field)}
                  title={`{${doc.field}} — ${fieldLabel(doc.field)}`}
                >
                  <code class="shrink-0 text-code text-primary">{`{${doc.field}}`}</code>
                  <span class="min-w-0 flex-1 truncate text-caption text-muted-foreground">
                    {fieldLabel(doc.field)}
                  </span>
                  <span
                    class="max-w-36 shrink-0 truncate text-caption text-faint-foreground"
                    title={doc.example}
                  >
                    {doc.example}
                  </span>
                </button>
              {/each}
            </div>
          {/if}
          <div class="flex flex-col gap-1 border-t border-border px-3 py-2.5">
            {#each TEMPLATE_FILTERS as filter (filter.syntax)}
              <div class="flex min-w-0 items-baseline gap-2">
                <code class="shrink-0 text-code">{filter.syntax}</code>
                <span class="min-w-0 flex-1 truncate text-caption text-muted-foreground">
                  {t(filter.key)}
                </span>
                <code class="shrink-0 text-caption text-faint-foreground">{filter.example}</code>
              </div>
            {/each}
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span class="text-caption text-muted-foreground">{t("settings.preview")}</span>
            {#if fieldSample?.title}
              <span class="text-caption text-faint-foreground">
                {t("settings.previewSample", {
                  artist: fieldSample.artist ?? "",
                  title: fieldSample.title,
                })}
              </span>
            {/if}
          </div>
          {#if previewParts}
            <code
              class="rounded-control border border-border bg-surface-subtle p-3 text-code break-all"
              ><span class="text-faint-foreground">{previewParts.root}</span><span
                >{previewParts.rest}</span
              ></code
            >
          {:else if !loadError}
            <p
              class="rounded-control border border-border bg-surface-subtle p-3 text-code break-all text-faint-foreground"
            >
              {t("common.loading")}
            </p>
          {/if}
        </div>

        {#if previewError}
          <Note tone="fail">{previewError}</Note>
        {/if}

        <div class="flex items-center gap-3">
          <Button size="lg" disabled={saving || !loaded} onclick={() => void save()}>
            {saving ? t("settings.saving") : t("settings.save")}
          </Button>
          {#if savedKey !== null && !dirty}
            <Note tone="done">{t("settings.saved")}</Note>
          {/if}
        </div>

        {#if saveError}
          <Note tone="fail">{saveError}</Note>
        {/if}
      </div>
    </SectionCard>

    <SectionCard title={t("settings.downloadSection")} icon={DownloadIcon}>
      <div class="flex flex-col gap-4">
        <div class="grid gap-4 sm:grid-cols-2">
          <Field
            label={t("settings.maxTasks")}
            for="setting-max-tasks"
            hint={t("settings.maxTasksHint")}
          >
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
        </div>

        <!-- 占用与上限同屏：改完上限保存即刷新上面那行数字，「清理缓存」只删缓存目录 -->
        <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border pt-4">
          <div class="min-w-0">
            <p class="text-body font-medium">{t("settings.cacheUsage")}</p>
            <p class="min-w-0 text-caption text-muted-foreground">
              {cacheDetail ?? t("common.loading")}
            </p>
          </div>
          <div class="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              disabled={clearing || cacheStats === null || cacheStats.total_bytes === 0}
              onclick={() => void clearCache()}
            >
              {clearing ? t("settings.cacheClearing") : t("settings.cacheClear")}
            </Button>
            {#if cacheNote}
              <Note tone={cacheNote.tone}>{cacheNote.text}</Note>
            {/if}
          </div>
        </div>

        <p class="text-caption text-muted-foreground">{t("settings.restartHint")}</p>
      </div>
    </SectionCard>

    <SectionCard
      title={t("settings.chkszSection")}
      hint={t("settings.chkszHint")}
      icon={CloudIcon}
    >
      <div class="flex flex-col gap-4">
        <Field
          label={t("settings.chkszEnabled")}
          for="setting-chksz-enabled"
          hint={t("settings.chkszEnabledHint")}
        >
          <div class="flex items-center gap-3">
            <Checkbox
              id="setting-chksz-enabled"
              checked={chkszEnabled}
              onCheckedChange={(value) => (chkszEnabled = value === true)}
            />
            <span class="text-body">{chkszEnabled ? t("settings.chkszOn") : t("settings.chkszOff")}</span>
          </div>
        </Field>

        <!-- Key 只写不回显：服务端只回「有没有」（NFR-02），留空 = 不改动 -->
        <Field
          label={t("settings.chkszKey")}
          for="setting-chksz-key"
          hint={hasChkszKey ? t("settings.chkszKeySet") : t("settings.chkszKeyHint")}
        >
          <div class="flex flex-wrap items-center gap-3">
            <Input
              id="setting-chksz-key"
              type="password"
              autocomplete="off"
              class="w-72"
              placeholder={hasChkszKey ? "••••••••" : "chksz_…"}
              bind:value={chkszKey}
            />
            <Button
              size="sm"
              disabled={savingChkszKey || chkszKey.trim() === ""}
              onclick={() => void saveChkszKey()}
            >
              {savingChkszKey ? t("settings.saving") : t("settings.chkszKeySave")}
            </Button>
          </div>
        </Field>

        {#if chkszEnabled}
          <div class="grid gap-4 sm:grid-cols-2">
            <Field
              label={t("settings.chkszDownloadQuality")}
              for="setting-chksz-download"
              hint={t("settings.chkszDownloadQualityHint")}
            >
              <Select
                type="single"
                value={chkszDownloadQuality}
                items={chkszTierItems}
                onValueChange={(value) => (chkszDownloadQuality = value as QualityTier)}
              >
                <SelectTrigger id="setting-chksz-download" class="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {#each chkszTiers as option (option.tier)}
                    <SelectItem value={option.tier}>{option.label}</SelectItem>
                  {/each}
                </SelectContent>
              </Select>
            </Field>

            <Field
              label={t("settings.chkszPreviewQuality")}
              for="setting-chksz-preview"
              hint={t("settings.chkszPreviewQualityHint")}
            >
              <Select
                type="single"
                value={chkszPreviewQuality}
                items={chkszTierItems}
                onValueChange={(value) => (chkszPreviewQuality = value as QualityTier)}
              >
                <SelectTrigger id="setting-chksz-preview" class="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {#each chkszTiers as option (option.tier)}
                    <SelectItem value={option.tier}>{option.label}</SelectItem>
                  {/each}
                </SelectContent>
              </Select>
            </Field>
          </div>
        {/if}

        {#if chkszNote}
          <Note tone={chkszNote.tone}>{chkszNote.text}</Note>
        {/if}
        <p class="text-caption text-muted-foreground">{t("settings.chkszGetKey")}</p>
      </div>
    </SectionCard>

    <SectionCard
      title={t("settings.interfaceSection")}
      hint={t("settings.interfaceHint")}
      icon={PaletteIcon}
    >
      <div class="flex flex-col gap-4">
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

        {#if session.web?.required}
          <div class="flex items-center justify-between gap-3 border-t border-border pt-4">
            <div class="min-w-0">
              <p class="text-body font-medium">{t("settings.webLogout")}</p>
              <p class="text-caption text-muted-foreground">{t("settings.webLogoutHint")}</p>
            </div>
            <Button variant="outline" size="sm" onclick={() => void session.webLogout()}>
              {t("settings.webLogout")}
            </Button>
          </div>
        {/if}
      </div>
    </SectionCard>
  </div>

  <div class="flex min-w-0 flex-col gap-4 md:gap-6">
    <SectionCard
      title={t("settings.accountSection")}
      hint={t("settings.accountHint")}
      icon={CircleUserIcon}
    >
      <div class="flex flex-col gap-4">
        <div
          class="flex flex-wrap items-center gap-3 rounded-control border border-border px-3 py-2.5"
        >
          <Lamp
            tone={me?.connected ? "done" : "fail"}
            label={me?.connected ? t("app.connected") : t("app.disconnected")}
          />
          {#if me?.connected}
            <span class="text-body font-semibold">{accountName}</span>
            {#if me.username}
              <span class="tabular text-caption text-muted-foreground">@{me.username}</span>
            {/if}
            {#if me.premium}
              <span
                class="rounded-full bg-primary-soft px-1.5 text-caption font-medium text-primary"
              >
                {t("settings.accountPremium")}
              </span>
            {/if}
          {:else}
            <span class="text-caption text-muted-foreground"
              >{t("settings.accountNotConnected")}</span
            >
          {/if}
        </div>

        {#if me?.connected}
          <div class="flex items-center gap-3">
            <Button variant="destructive" size="lg" onclick={() => (logoutOpen = true)}>
              {t("settings.logout")}
            </Button>
            <p class="text-caption text-muted-foreground">{t("settings.logoutHint")}</p>
          </div>
        {:else}
          <div class="flex items-center gap-3">
            <Button size="lg" onclick={() => navigate(pathOf("setup"))}>
              {t("settings.accountGoSetup")}
            </Button>
          </div>
        {/if}

        {#if accountNote}
          <Note tone={accountNote.tone}>{accountNote.text}</Note>
        {/if}
      </div>
    </SectionCard>

    <SectionCard title={t("settings.botSection")} hint={t("settings.botHint")} icon={BotIcon}>
      <div class="flex flex-col gap-4">
        <div
          class="flex flex-wrap items-center gap-3 rounded-control border border-border px-3 py-2.5"
        >
          <Lamp
            tone={status?.has_bot_token ? "done" : "idle"}
            label={status?.has_bot_token ? t("settings.botSet") : t("settings.botUnset")}
          />
          <span class="text-caption text-muted-foreground">{t("settings.botReadOnly")}</span>
        </div>

        <Field
          label={t("settings.botToken")}
          for="setting-bot-token"
          hint={t("settings.botTokenHint")}
        >
          <div class="flex items-center gap-2">
            <Input
              id="setting-bot-token"
              class="min-w-0 flex-1"
              autocomplete="off"
              placeholder={t("settings.botTokenPlaceholder")}
              bind:value={botToken}
            />
            <Button
              size="lg"
              disabled={savingBot || botToken.trim() === ""}
              onclick={() => void saveBot()}
            >
              {savingBot ? t("settings.botSaving") : t("settings.botSave")}
            </Button>
          </div>
        </Field>

        {#if botNote}
          <Note tone={botNote.tone}>{botNote.text}</Note>
        {/if}
      </div>
    </SectionCard>

    <SectionCard
      title={t("settings.proxySection")}
      hint={t("settings.proxyHint")}
      icon={NetworkIcon}
    >
      <div class="flex flex-col gap-4">
        <div
          class="flex flex-wrap items-center gap-3 rounded-control border border-border px-3 py-2.5"
        >
          <Lamp tone={status?.proxy?.hostname ? "done" : "idle"} label={proxySummary} />
        </div>

        <label class="flex items-center gap-2 text-body">
          <Checkbox bind:checked={useProxy} />
          {t("settings.proxyUse")}
        </label>

        {#if useProxy}
          <div class="flex flex-col gap-3">
            <div class="grid gap-3 sm:grid-cols-2">
              <Field label={t("settings.proxyScheme")}>
                <Select type="single" bind:value={proxyScheme}>
                  <SelectTrigger class="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="socks5">SOCKS5</SelectItem>
                    <SelectItem value="http">HTTP</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label={t("settings.proxyHost")} for="setting-proxy-host">
                <Input
                  id="setting-proxy-host"
                  placeholder={t("settings.proxyHostPlaceholder")}
                  bind:value={proxyHost}
                />
              </Field>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <Field label={t("settings.proxyPort")} for="setting-proxy-port">
                <Input
                  id="setting-proxy-port"
                  class="tabular"
                  inputmode="numeric"
                  bind:value={proxyPort}
                />
              </Field>
              <Field
                label={t("settings.proxyUser")}
                for="setting-proxy-user"
                hint={t("settings.proxyKeep")}
              >
                <Input id="setting-proxy-user" autocomplete="off" bind:value={proxyUser} />
              </Field>
            </div>
            <Field label={t("settings.proxyPass")} for="setting-proxy-pass">
              <Input
                id="setting-proxy-pass"
                type="password"
                autocomplete="off"
                bind:value={proxyPass}
              />
            </Field>
          </div>
        {/if}

        <div class="flex items-center gap-3">
          <Button size="lg" disabled={savingProxy} onclick={() => void saveProxy()}>
            {savingProxy ? t("settings.proxySaving") : t("settings.proxySave")}
          </Button>
        </div>

        {#if proxyNote}
          <Note tone={proxyNote.tone}>{proxyNote.text}</Note>
        {/if}
      </div>
    </SectionCard>

    <SectionCard
      title={t("settings.resetSection")}
      hint={t("settings.resetHint")}
      icon={RotateCcwIcon}
    >
      <div class="flex items-center gap-3">
        <Button variant="destructive" size="lg" onclick={() => (resetOpen = true)}>
          {t("settings.reset")}
        </Button>
      </div>
    </SectionCard>
  </div>
</div>

<Dialog bind:open={logoutOpen}>
  <DialogContent>
    <DialogHeader>
      <DialogTitle class="text-h2 font-semibold">{t("settings.logoutTitle")}</DialogTitle>
      <DialogDescription class="text-caption">{t("settings.logoutBody")}</DialogDescription>
    </DialogHeader>
    <DialogFooter>
      <Button variant="outline" onclick={() => (logoutOpen = false)}>{t("common.cancel")}</Button>
      <Button
        variant="destructive"
        size="lg"
        disabled={loggingOut}
        onclick={() => void confirmLogout()}
      >
        {loggingOut ? t("settings.loggingOut") : t("settings.logoutConfirm")}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>

<Dialog
  bind:open={resetOpen}
  onOpenChange={(open) => {
    if (open) resetError = "";
  }}
>
  <DialogContent>
    <DialogHeader>
      <DialogTitle class="text-h2 font-semibold">{t("settings.resetTitle")}</DialogTitle>
      <DialogDescription class="text-caption">{t("settings.resetBody")}</DialogDescription>
    </DialogHeader>
    {#if resetError}
      <Note tone="fail">{resetError}</Note>
    {/if}
    <DialogFooter>
      <Button variant="outline" onclick={() => (resetOpen = false)}>{t("common.cancel")}</Button>
      <Button
        variant="destructive"
        size="lg"
        disabled={resetting}
        onclick={() => void confirmReset()}
      >
        {resetting ? t("settings.resetting") : t("settings.resetConfirm")}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
