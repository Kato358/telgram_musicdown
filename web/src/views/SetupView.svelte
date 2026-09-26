<script lang="ts">
  /** 初始化向导（FR-OPS-02）：三步——密钥与代理 → 登录账号 → 音乐源（源可选）。
   *
   * 步骤编号在此是真序列，故允许编号（设计规范 §10）；本页是唯一不套侧栏/顶栏/播放条
   * 的全屏闸门，自带页面容器（§2.2）。事实源都在服务端：密钥在 config.yaml、会话在
   * sessions/、音乐源在 DB——本页只做表单、校验与状态映射，不缓存副本。
   */
  import { onMount } from "svelte";
  import CircleAlertIcon from "@lucide/svelte/icons/circle-alert";
  import LightbulbIcon from "@lucide/svelte/icons/lightbulb";
  import LockIcon from "@lucide/svelte/icons/lock";
  import MusicIcon from "@lucide/svelte/icons/music";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import RefreshCwIcon from "@lucide/svelte/icons/refresh-cw";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SendIcon from "@lucide/svelte/icons/send";
  import { ApiError, api, errorText } from "$lib/api/client";
  import type {
    DiscoverCandidate,
    DiscoverResponse,
    MeResponse,
    SendCodeResponse,
    SetupSecretsPayload,
    SourceRow,
  } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { API_HASH_RE, API_ID_RE, BOT_TOKEN_RE, MAX_PORT, PORT_RE } from "$lib/secrets";
  import { session } from "$lib/stores/session.svelte";
  import { formatCount } from "$lib/format";
  import type { Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import { Checkbox } from "$lib/components/ui/checkbox";
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

  type Step = 1 | 2 | 3;
  type Tab = "rec" | "manual";
  type Feedback = { tone: Tone; text: string };
  type AddResult = { ok: true; row: SourceRow } | { ok: false; error: string };

  const STEPS: Step[] = [1, 2, 3];
  const REC_PAGE = 4; // 推荐每次显示 4 条，「换一批」翻页

  let step = $state<Step>(1);
  let tab = $state<Tab>("rec");

  let savingKeys = $state(false);
  let sendingCode = $state(false);
  let signingIn = $state(false);
  let adding = $state(false);
  let loadingRec = $state(false);
  let removingId = $state<number | null>(null);

  let keysNote = $state<Feedback | null>(null);
  let loginNote = $state<Feedback | null>(null);
  let recNote = $state<Feedback | null>(null);
  let manualNote = $state<Feedback | null>(null);
  /** 页面级提示：状态/账号接口拉不到（401、后端未起）时说明原因，不退化成空白向导。 */
  let pageNote = $state<Feedback | null>(null);

  let apiId = $state("");
  let apiHash = $state("");
  let botToken = $state("");
  let useProxy = $state(false);
  let proxyScheme = $state("socks5");
  let proxyHost = $state("");
  let proxyPort = $state("1080");
  let proxyUser = $state("");
  let proxyPass = $state("");

  let phone = $state("");
  let code = $state("");
  let codeHash = $state("");
  let password = $state("");
  let needPassword = $state(false);
  let me = $state<MeResponse | null>(null);

  let link = $state("");
  let candidates = $state<DiscoverCandidate[]>([]);
  let recLoaded = $state(false);
  let chips = $state<string[]>([]);
  let q = $state("");
  let batch = $state(0);
  let sources = $state<SourceRow[]>([]);

  const status = $derived(session.setup);
  const keysSaved = $derived(Boolean(status?.has_api_id && status?.has_api_hash));
  const connected = $derived(Boolean(status?.connected));
  const handle = $derived(me?.username ? `@${me.username}` : (me?.display_name ?? ""));

  /** 账号行文案：拿不到账号信息时不假装有句柄（只说已连接）。 */
  const connectedLabel = $derived(handle ? t("setup.status.connectedAs", { handle }) : t("app.connected"));
  const addedIds = $derived(sources.map((row) => row.telegram_chat_id));

  /** 步骤是否已满足：第 3 步的源可选，只要加过就算满足。 */
  function stepDone(n: Step): boolean {
    if (n === 1) return keysSaved;
    if (n === 2) return connected;
    return sources.length > 0;
  }

  function stepLabel(n: Step): string {
    if (n === 1) return t("setup.steps.keys");
    return n === 2 ? t("setup.steps.login") : t("setup.steps.sources");
  }

  function stepSub(n: Step): string {
    if (n === 1) {
      if (keysSaved) return t("setup.steps.keysDone");
      return apiId.trim() || apiHash.trim() ? t("setup.steps.keysTyping") : t("setup.steps.keysTodo");
    }
    if (n === 2) {
      if (connected) return t("setup.steps.loginDone");
      return codeHash || sendingCode ? t("setup.steps.loginDoing") : t("setup.steps.loginTodo");
    }
    if (sources.length > 0) return t("setup.steps.sourcesDone", { n: sources.length });
    return step === 3 ? t("setup.steps.sourcesDoing") : t("setup.steps.sourcesTodo");
  }

  const statusRows = $derived.by(() => {
    const proxy = status?.proxy ?? null;
    const proxyText = !proxy
      ? t("setup.status.proxyDirect")
      : proxy.hostname
        ? `${proxy.scheme.toUpperCase()} ${proxy.hostname}:${proxy.port}`
        : t("setup.status.proxyHostMissing");
    return [
      {
        key: t("setup.status.apiId"),
        hint: t("setup.status.apiIdHint"),
        tone: (status?.has_api_id ? "done" : "fail") as Tone,
        text: status?.has_api_id ? t("setup.status.ready") : t("setup.status.missing"),
      },
      {
        key: t("setup.status.apiHash"),
        hint: t("setup.status.apiHashHint"),
        tone: (status?.has_api_hash ? "done" : "fail") as Tone,
        text: status?.has_api_hash ? t("setup.status.ready") : t("setup.status.missing"),
      },
      {
        key: t("setup.status.proxy"),
        hint: t("setup.status.proxyHint"),
        tone: (proxy?.hostname ? "done" : "idle") as Tone,
        text: proxyText,
      },
      {
        key: t("setup.status.botToken"),
        hint: t("setup.status.botTokenHint"),
        tone: (status?.has_bot_token ? "done" : "idle") as Tone,
        text: status?.has_bot_token ? t("setup.status.botSet") : t("setup.status.botUnset"),
      },
      {
        key: t("setup.status.botAuth"),
        hint: t("setup.status.botAuthHint"),
        tone: (connected ? "done" : "idle") as Tone,
        text: connected ? t("setup.status.botAuthFollows") : t("setup.status.botAuthPending"),
      },
      {
        key: t("setup.status.connection"),
        hint: t("setup.status.connectionHint"),
        tone: (connected ? "done" : "fail") as Tone,
        text: connected
          ? connectedLabel
          : codeHash
            ? t("setup.status.waitingCode")
            : t("setup.status.notConnected"),
      },
      {
        key: t("setup.status.sources"),
        hint: t("setup.status.sourcesHint"),
        tone: (sources.length > 0 ? "done" : "idle") as Tone,
        text: sources.length
          ? t("setup.status.sourcesCount", { n: sources.length })
          : t("setup.status.noSources"),
      },
    ];
  });

  /** 标签按候选命中次数排序：先给最可能的那几个，避免出现一次性标签。 */
  const tags = $derived.by(() => {
    const count: Record<string, number> = {};
    for (const item of candidates) {
      for (const tag of item.tags) count[tag] = (count[tag] ?? 0) + 1;
    }
    return Object.entries(count)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([tag]) => tag);
  });

  /** 推荐池：标签（任一命中）与关键词（标题/用户名）叠加过滤，并排除已添加的。 */
  const pool = $derived.by(() => {
    const needle = q.trim().toLowerCase();
    return candidates.filter((item) => {
      if (addedIds.includes(item.chat_id)) return false;
      if (chips.length > 0 && !item.tags.some((tag) => chips.includes(tag))) return false;
      if (needle === "") return true;
      return (
        item.title.toLowerCase().includes(needle) ||
        (item.username ?? "").toLowerCase().includes(needle)
      );
    });
  });

  const page = $derived.by(() => {
    const size = Math.min(REC_PAGE, pool.length);
    const out: DiscoverCandidate[] = [];
    for (let i = 0; i < size; i += 1) out.push(pool[(batch * REC_PAGE + i) % pool.length]);
    return out;
  });

  const gateNote = $derived(
    step === 1 && !keysSaved
      ? t("setup.gateNeedKeys")
      : step === 3 && sources.length === 0
        ? t("setup.gateNoSources")
        : "",
  );

  /** 后端错误码 → 本地文案；认不出就原样显示后端 message（含修复提示）。 */
  function authErrorText(err: unknown): string {
    if (err instanceof ApiError) {
      const map: Record<string, string> = {
        secrets_missing: t("setup.needKeysFirst"),
        phone_invalid: t("setup.phoneInvalid"),
        phone_banned: t("setup.phoneBanned"),
        code_invalid: t("setup.codeInvalid"),
        code_expired: t("setup.codeExpired"),
        password_required: t("setup.passwordRequired"),
        password_invalid: t("setup.passwordInvalid"),
        not_connected: t("setup.recNeedLogin"),
      };
      return map[err.code] ?? err.detail;
    }
    return errorText(err, t("common.error"));
  }

  function handleOf(row: SourceRow): string {
    if (!row.username) return String(row.telegram_chat_id);
    return row.username.startsWith("@") ? row.username : `@${row.username}`;
  }

  /** 候选行的入参：有用户名用用户名，私密对话退回 chat_id（后端两种都认）。 */
  function linkOf(item: DiscoverCandidate): string {
    return item.username ? `@${item.username}` : String(item.chat_id);
  }

  async function loadSources() {
    try {
      sources = await api.get<SourceRow[]>("/api/sources");
    } catch (err) {
      recNote = { tone: "fail", text: authErrorText(err) };
    }
  }

  async function loadCandidates() {
    if (!connected) {
      candidates = [];
      return;
    }
    loadingRec = true;
    try {
      const resp = await api.get<DiscoverResponse>("/api/sources/discover");
      candidates = resp.items;
      recNote = null;
    } catch (err) {
      recNote = { tone: "fail", text: authErrorText(err) };
    } finally {
      recLoaded = true;
      loadingRec = false;
    }
  }

  /** 刷新三个事实源（状态、账号、源列表），并同步候选池与当前步骤。
   *
   * 状态/账号接口失败时（401、后端没起来）仍留在向导：`pageNote` 写清原因，
   * 各步的表单与重试照常可用——首次部署没有别的入口。
   */
  async function refresh(options: { keepStep?: boolean } = {}) {
    try {
      await Promise.all([session.loadSetup(), session.loadMe(), loadSources()]);
      pageNote = null;
    } catch (err) {
      pageNote = { tone: "fail", text: authErrorText(err) };
    }
    me = session.me;
    if (!(options.keepStep ?? false)) {
      // 首次进入落在第一个未满足的步骤：续做的人不必从第 1 步翻起
      step = STEPS.find((n) => !stepDone(n)) ?? 3;
    }
  }

  function savedItems(): string[] {
    const items: string[] = [];
    if (useProxy) {
      items.push(
        t("setup.savedItemProxy", {
          scheme: proxyScheme.toUpperCase(),
          host: proxyHost.trim(),
          port: proxyPort.trim(),
        }),
      );
    }
    if (apiId.trim() || apiHash.trim()) items.push("api_id / api_hash");
    if (botToken.trim()) items.push("bot_token");
    return items;
  }

  /** 前端校验只为即时反馈；写盘前的判据在服务端（同一套规则）。 */
  function keyProblems(): string[] {
    const problems: string[] = [];
    const id = apiId.trim();
    const hash = apiHash.trim();
    if (id !== "" || hash !== "" || !keysSaved) {
      if (!API_ID_RE.test(id)) problems.push(t("setup.apiIdInvalid"));
      if (!API_HASH_RE.test(hash)) problems.push(t("setup.apiHashInvalid"));
    }
    if (botToken.trim() !== "" && !BOT_TOKEN_RE.test(botToken.trim())) {
      problems.push(t("setup.botTokenInvalid"));
    }
    if (useProxy) {
      if (proxyHost.trim() === "") problems.push(t("setup.proxyHostRequired"));
      const port = Number(proxyPort.trim());
      if (!PORT_RE.test(proxyPort.trim()) || port < 1 || port > MAX_PORT) {
        problems.push(t("setup.proxyPortInvalid"));
      }
    }
    return problems;
  }

  async function saveKeys() {
    if (savingKeys) return;
    const problems = keyProblems();
    if (problems.length > 0) {
      keysNote = {
        tone: "fail",
        text: t("setup.keysInvalidOne", { items: problems.join(t("setup.listSep")) }),
      };
      return;
    }
    savingKeys = true;
    keysNote = null;
    try {
      const payload: SetupSecretsPayload = {};
      if (apiId.trim()) payload.api_id = Number(apiId.trim());
      if (apiHash.trim()) payload.api_hash = apiHash.trim();
      if (botToken.trim()) payload.bot_token = botToken.trim();
      payload.proxy = useProxy
        ? {
            scheme: proxyScheme,
            hostname: proxyHost.trim(),
            port: Number(proxyPort.trim()),
            ...(proxyUser.trim() ? { username: proxyUser.trim() } : {}),
            ...(proxyPass ? { password: proxyPass } : {}),
          }
        : null;
      const resp = await api.post<{ restart_required: boolean }>("/api/setup/secrets", payload);
      await refresh({ keepStep: true });
      const items = savedItems();
      if (items.length === 0) {
        keysNote = { tone: "wait", text: t("setup.keysUnchanged") };
      } else {
        const key = resp.restart_required
          ? botToken.trim()
            ? "setup.keysSavedBot"
            : "setup.keysSaved"
          : "setup.keysSavedFresh";
        keysNote = {
          tone: "done",
          text: t(key, { items: items.join(t("setup.listSep")) }),
        };
      }
    } catch (err) {
      keysNote = { tone: "fail", text: authErrorText(err) };
    } finally {
      savingKeys = false;
    }
  }

  async function sendCode() {
    if (sendingCode) return;
    const raw = phone.trim();
    if (!/^\+?\d{6,15}$/.test(raw.replace(/[\s\-()]/g, ""))) {
      loginNote = { tone: "fail", text: t("setup.phoneInvalid") };
      return;
    }
    sendingCode = true;
    loginNote = null;
    try {
      const resp = await api.post<SendCodeResponse>("/api/auth/telegram/send-code", {
        phone: raw,
      });
      if (resp.authorized) {
        await refresh();
        loginNote = { tone: "done", text: t("setup.alreadyLoggedIn") };
        await loadCandidates();
      } else {
        codeHash = resp.code_hash;
        loginNote = { tone: "wait", text: t("setup.codeSent") };
      }
    } catch (err) {
      loginNote = { tone: "fail", text: authErrorText(err) };
    } finally {
      sendingCode = false;
    }
  }

  async function signIn() {
    if (signingIn) return;
    signingIn = true;
    loginNote = null;
    try {
      await api.post("/api/auth/telegram/sign-in", {
        phone: phone.trim(),
        code: code.trim(),
        code_hash: codeHash,
        ...(needPassword ? { password } : {}),
      });
      await refresh();
      loginNote = { tone: "done", text: t("setup.loginDoneNote", { handle }) };
      await loadCandidates();
    } catch (err) {
      if (err instanceof ApiError && err.code === "password_required") {
        needPassword = true;
      }
      loginNote = { tone: "fail", text: authErrorText(err) };
    } finally {
      signingIn = false;
    }
  }

  async function addSource(linkValue: string): Promise<AddResult> {
    if (adding) return { ok: false, error: "" };
    adding = true;
    try {
      const row = await api.post<SourceRow>("/api/sources", { link: linkValue });
      await loadSources();
      return { ok: true, row };
    } catch (err) {
      return { ok: false, error: authErrorText(err) };
    } finally {
      adding = false;
    }
  }

  async function addRecommended(item: DiscoverCandidate) {
    recNote = null;
    const result = await addSource(linkOf(item));
    if (!result.ok) {
      if (result.error) recNote = { tone: "fail", text: result.error };
      return;
    }
    recNote = { tone: "done", text: t("setup.added", { handle: handleOf(result.row) }) };
  }

  async function addManual() {
    const raw = link.trim();
    if (raw === "") {
      manualNote = { tone: "fail", text: t("setup.linkUnrecognized") };
      return;
    }
    if (/t\.me\/(joinchat|\+)/i.test(raw) || raw.startsWith("+")) {
      manualNote = { tone: "fail", text: t("setup.inviteUnsupported") };
      return;
    }
    const match =
      raw.match(/^(?:https?:\/\/)?(?:t\.me|telegram\.me)\/([A-Za-z0-9_]{5,32})\/?$/) ??
      raw.match(/^@?([A-Za-z0-9_]{5,32})$/);
    if (!match) {
      manualNote = { tone: "fail", text: t("setup.linkUnrecognized") };
      return;
    }
    const name = match[1];
    if (sources.some((row) => row.username === name)) {
      manualNote = { tone: "fail", text: t("setup.alreadyAdded", { handle: `@${name}` }) };
      return;
    }
    manualNote = null;
    const result = await addSource(`@${name}`);
    if (!result.ok) {
      if (result.error) manualNote = { tone: "fail", text: result.error };
      return;
    }
    link = "";
    manualNote = { tone: "done", text: t("setup.manualAdded", { handle: handleOf(result.row) }) };
  }

  async function removeSource(row: SourceRow) {
    if (removingId !== null) return;
    removingId = row.id;
    recNote = null;
    try {
      await api.delete<{ ok: boolean }>(`/api/sources/${row.id}?with_history=false`);
      await loadSources();
    } catch (err) {
      recNote = { tone: "fail", text: authErrorText(err) };
    } finally {
      removingId = null;
    }
  }

  function toggleChip(tag: string) {
    chips = chips.includes(tag) ? chips.filter((item) => item !== tag) : [...chips, tag];
    batch = 0;
  }

  /** 下一步的闸门与设计稿一致：第 1 步要密钥、第 2 步要登录，第 3 步源可选。 */
  const nextDisabled = $derived(
    step === 1 ? !keysSaved : step === 2 ? !connected : false,
  );

  function goPrev() {
    if (step > 1) step = (step - 1) as Step;
  }

  async function goNext() {
    if (nextDisabled) return;
    if (step < 3) {
      step = (step + 1) as Step;
      return;
    }
    await session.loadSetup();
    navigate(pathOf("dashboard"));
  }

  onMount(() => {
    void (async () => {
      await refresh();
      // 已完成过初始化的人带着残留地址落进向导（登录闸门误导航过、手工输 /setup）：
      // 送回控制台。只在进场判一次——第 2 步登录成功后 complete 就已是 true，
      // 跟着状态跳会把第 3 步（音乐源）直接跳没。
      if (session.setup?.complete) {
        navigate(pathOf("dashboard"), { replace: true });
        return;
      }
      await loadCandidates();
    })();
  });
</script>

<div class="mx-auto flex min-h-dvh w-full max-w-[1120px] flex-col gap-5 p-4 md:gap-6 md:p-6">
  <header class="flex flex-col gap-1">
    <p class="tabular text-caption text-muted-foreground">{t("app.repo")}</p>
    <h1 class="text-h1 font-bold">{t("setup.title")}</h1>
    <p class="text-body text-muted-foreground">{t("setup.lede")}</p>
  </header>

  {#if pageNote}
    <Note tone={pageNote.tone}>{pageNote.text}</Note>
  {/if}

  <ol class="flex items-center">
    {#each STEPS as n (n)}
      {@const state = stepDone(n) ? "done" : step === n ? "active" : "todo"}
      <li
        class="flex min-w-0 items-center gap-2.5"
        aria-current={step === n ? "step" : undefined}
      >
        <span
          class="grid size-6.5 shrink-0 place-items-center rounded-full border text-caption font-semibold {state ===
          'active'
            ? 'border-primary bg-primary text-primary-foreground'
            : state === 'done'
              ? 'border-primary bg-card text-primary'
              : 'border-border bg-card text-muted-foreground'}"
          aria-hidden="true"
        >
          {#if state === "done"}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="3"
              stroke-linecap="round"
            >
              <path d="m5 13 4 4L19 7" />
            </svg>
          {:else}
            {n}
          {/if}
        </span>
        <span class="min-w-0">
          <span class="block truncate text-body font-semibold">{stepLabel(n)}</span>
          <span class="block truncate text-caption text-muted-foreground">{stepSub(n)}</span>
        </span>
      </li>
      {#if n < 3}
        <li
          class="mx-3.5 h-0.5 min-w-6 flex-1 {stepDone(n) ? 'bg-primary' : 'bg-border'}"
          aria-hidden="true"
        ></li>
      {/if}
    {/each}
  </ol>

  <div class="grid items-stretch gap-5 lg:grid-cols-2">
    <section class="card p-5" aria-labelledby="setup-state">
      <header class="mb-3.5 flex items-center gap-3">
        <span class="grid size-9 place-items-center rounded-control bg-primary-soft text-primary" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="3" y="3.5" width="18" height="7" rx="2" />
            <rect x="3" y="13.5" width="18" height="7" rx="2" />
          </svg>
        </span>
        <div>
          <h2 id="setup-state" class="text-h2 font-semibold">{t("setup.status.title")}</h2>
          <p class="text-caption text-muted-foreground">{t("setup.status.hint")}</p>
        </div>
      </header>
      <ul class="flex flex-col gap-2">
        {#each statusRows as row (row.key)}
          <li class="flex items-center gap-2.5 rounded-nav border border-border px-3 py-2.5">
            <span class="shrink-0 text-body font-medium">{row.key}</span>
            <span class="min-w-0 truncate text-caption text-muted-foreground">{row.hint}</span>
            <span class="ml-auto shrink-0">
              <Lamp tone={row.tone} label={row.text} />
            </span>
          </li>
        {/each}
      </ul>
    </section>

    <div class="flex flex-col lg:row-span-1">
      {#if step === 1}
        <section class="card flex flex-1 flex-col gap-3 p-5" aria-labelledby="setup-step1">
          <header class="flex items-center gap-3">
            <span class="grid size-9 place-items-center rounded-control bg-primary-soft text-primary" aria-hidden="true">
              <LockIcon class="size-4.5" />
            </span>
            <div>
              <h2 id="setup-step1" class="text-h2 font-semibold">{t("setup.step1Title")}</h2>
              <p class="text-caption text-muted-foreground">{t("setup.step1Hint")}</p>
            </div>
          </header>

          <div class="grid gap-3 sm:grid-cols-2">
            <Field label={t("setup.apiId")} for="setup-api-id">
              <Input
                id="setup-api-id"
                class="tabular"
                inputmode="numeric"
                placeholder={t("setup.apiIdPlaceholder")}
                bind:value={apiId}
              />
            </Field>
            <Field label={t("setup.apiHash")} for="setup-api-hash">
              <Input
                id="setup-api-hash"
                placeholder={t("setup.apiHashPlaceholder")}
                bind:value={apiHash}
              />
            </Field>
          </div>

          <Field label={t("setup.botToken")} for="setup-bot-token" hint={t("setup.botTokenHint")}>
            <Input
              id="setup-bot-token"
              placeholder={t("setup.botTokenPlaceholder")}
              bind:value={botToken}
            />
          </Field>

          <label class="flex items-center gap-2 text-body">
            <Checkbox bind:checked={useProxy} />
            {t("setup.useProxy")}
          </label>

          {#if useProxy}
            <div class="flex flex-col gap-3">
              <div class="grid gap-3 sm:grid-cols-2">
                <Field label={t("setup.proxyScheme")}>
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
                <Field label={t("setup.proxyHost")} for="setup-proxy-host">
                  <Input
                    id="setup-proxy-host"
                    placeholder={t("setup.proxyHostPlaceholder")}
                    bind:value={proxyHost}
                  />
                </Field>
              </div>
              <div class="grid gap-3 sm:grid-cols-2">
                <Field label={t("setup.proxyPort")} for="setup-proxy-port">
                  <Input id="setup-proxy-port" class="tabular" inputmode="numeric" bind:value={proxyPort} />
                </Field>
                <Field label={t("setup.proxyUser")} for="setup-proxy-user">
                  <Input
                    id="setup-proxy-user"
                    placeholder={t("setup.proxyUserPlaceholder")}
                    bind:value={proxyUser}
                  />
                </Field>
              </div>
              <Field label={t("setup.proxyPass")} for="setup-proxy-pass">
                <Input
                  id="setup-proxy-pass"
                  type="password"
                  placeholder={t("setup.proxyUserPlaceholder")}
                  bind:value={proxyPass}
                />
              </Field>
            </div>
          {/if}

          <div class="flex flex-wrap items-center gap-3">
            <Button size="lg" disabled={savingKeys} onclick={() => void saveKeys()}>
              {savingKeys ? t("setup.saving") : t("setup.save")}
            </Button>
            <span class="text-caption text-muted-foreground">{t("setup.keysFile")}</span>
          </div>
          {#if keysNote}<Note tone={keysNote.tone}>{keysNote.text}</Note>{/if}
        </section>
      {/if}

      {#if step === 2}
        <section class="card flex flex-1 flex-col gap-3 p-5" aria-labelledby="setup-step2">
          <header class="flex items-center gap-3">
            <span class="grid size-9 place-items-center rounded-control bg-primary-soft text-primary" aria-hidden="true">
              <SendIcon class="size-4.5" />
            </span>
            <div>
              <h2 id="setup-step2" class="text-h2 font-semibold">{t("setup.step2Title")}</h2>
              <p class="text-caption text-muted-foreground">{t("setup.step2Hint")}</p>
            </div>
          </header>

          <div class="flex items-start gap-2.5 rounded-nav bg-primary-surface px-3.5 py-3">
            <CircleAlertIcon class="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
            <p class="text-body text-muted-foreground">{t("setup.codeBanner")}</p>
          </div>

          {#if connected}
            <div class="flex items-center gap-2.5 rounded-nav border border-border px-3 py-2">
              <span
                class="grid size-8 shrink-0 place-items-center rounded-full bg-primary-soft text-body text-primary"
                aria-hidden="true"
              >
                {handle.replace("@", "").slice(0, 1) || "?"}
              </span>
              <span class="min-w-0">
                <span class="block truncate text-body font-medium">{me?.display_name ?? handle}</span>
                <span class="block truncate text-caption text-muted-foreground">
                  {t("setup.sessionStored", { handle })}
                </span>
              </span>
              <span class="ml-auto shrink-0">
                <Lamp tone="done" label={t("setup.sessionValid")} />
              </span>
            </div>
            <p class="text-caption text-muted-foreground">{t("setup.logoutHint")}</p>
          {:else}
            <Field label={t("setup.phone")} for="setup-phone">
              <div class="flex flex-wrap items-center gap-2">
                <Input
                  id="setup-phone"
                  class="tabular min-w-48 flex-1"
                  type="tel"
                  placeholder={t("setup.phonePlaceholder")}
                  bind:value={phone}
                />
                <Button
                  variant="outline"
                  size="lg"
                  disabled={sendingCode || phone.trim() === ""}
                  onclick={() => void sendCode()}
                >
                  {sendingCode
                    ? t("setup.sending")
                    : codeHash
                      ? t("setup.resend")
                      : t("setup.sendCode")}
                </Button>
              </div>
            </Field>

            {#if codeHash}
              <Field label={t("setup.code")} for="setup-code">
                <Input
                  id="setup-code"
                  class="tabular"
                  inputmode="numeric"
                  placeholder={t("setup.codePlaceholder")}
                  bind:value={code}
                />
              </Field>
              {#if needPassword}
                <Field label={t("setup.password2fa")} for="setup-password">
                  <Input
                    id="setup-password"
                    type="password"
                    placeholder={t("setup.passwordPlaceholder")}
                    bind:value={password}
                  />
                </Field>
              {/if}
              <Button size="lg" class="self-start" disabled={signingIn} onclick={() => void signIn()}>
                {signingIn ? t("setup.loggingIn") : t("setup.login")}
              </Button>
            {/if}
          {/if}
          {#if loginNote}<Note tone={loginNote.tone}>{loginNote.text}</Note>{/if}
        </section>
      {/if}

      {#if step === 3}
        <section class="card flex flex-1 flex-col gap-3 p-5" aria-labelledby="setup-step3">
          <header class="flex items-center gap-3">
            <span class="grid size-9 place-items-center rounded-control bg-primary-soft text-primary" aria-hidden="true">
              <MusicIcon class="size-4.5" />
            </span>
            <div>
              <h2 id="setup-step3" class="text-h2 font-semibold">{t("setup.step3Title")}</h2>
              <p class="text-caption text-muted-foreground">{t("setup.step3Hint")}</p>
            </div>
          </header>

          <div class="flex items-start gap-2.5 rounded-nav bg-primary-surface px-3.5 py-3">
            <CircleAlertIcon class="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
            <p class="text-body text-muted-foreground">{t("setup.sourcesBanner")}</p>
          </div>

          <div
            class="flex gap-1 rounded-full border border-border bg-surface-subtle p-1"
            role="tablist"
            aria-label={t("setup.tablistLabel")}
          >
            <button
              type="button"
              role="tab"
              class="h-8 flex-1 rounded-full text-caption {tab === 'rec'
                ? 'bg-primary-soft font-medium text-primary'
                : 'text-muted-foreground'}"
              aria-selected={tab === "rec"}
              onclick={() => (tab = "rec")}
            >
              {t("setup.tabRec")}
            </button>
            <button
              type="button"
              role="tab"
              class="h-8 flex-1 rounded-full text-caption {tab === 'manual'
                ? 'bg-primary-soft font-medium text-primary'
                : 'text-muted-foreground'}"
              aria-selected={tab === "manual"}
              onclick={() => (tab = "manual")}
            >
              {t("setup.tabManual")}
            </button>
          </div>

          {#if tab === "rec"}
            <div class="flex flex-col gap-3">
              <p class="text-caption text-muted-foreground">{t("setup.recHint")}</p>

              {#if !connected}
                <Note tone="wait">{t("setup.recNeedLogin")}</Note>
              {:else}
                {#if tags.length > 0}
                  <div class="flex flex-wrap gap-2">
                    {#each tags as tag (tag)}
                      <button
                        type="button"
                        class="h-7 rounded-chip border px-2.5 text-caption {chips.includes(tag)
                          ? 'border-transparent bg-primary-soft text-primary'
                          : 'border-border bg-card text-muted-foreground'}"
                        aria-pressed={chips.includes(tag)}
                        onclick={() => toggleChip(tag)}
                      >
                        {tag}
                      </button>
                    {/each}
                  </div>
                {/if}

                <div class="relative">
                  <SearchIcon
                    class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint-foreground"
                    aria-hidden="true"
                  />
                  <Input
                    class="pl-9"
                    type="search"
                    aria-label={t("setup.recSearchLabel")}
                    placeholder={t("setup.recSearch")}
                    bind:value={q}
                    oninput={() => (batch = 0)}
                  />
                </div>

                <ul class="flex flex-col gap-2">
                  {#each page as item (item.chat_id)}
                    <li class="flex items-center gap-3 rounded-nav border border-border px-3 py-2.5">
                      <span
                        class="grid size-9 shrink-0 place-items-center rounded-full bg-primary-soft text-primary"
                        aria-hidden="true"
                      >
                        <MusicIcon class="size-4" />
                      </span>
                      <span class="min-w-0">
                        <span class="block truncate text-body font-medium">{item.title}</span>
                        <span class="block truncate text-caption text-muted-foreground">
                          {item.username ? `@${item.username}` : item.type}{item.members !== null
                            ? ` · ${t("setup.members", { n: formatCount(item.members) })}`
                            : ""}
                        </span>
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        class="ml-auto shrink-0"
                        disabled={adding}
                        onclick={() => void addRecommended(item)}
                      >
                        <PlusIcon class="size-3.5" aria-hidden="true" />
                        {adding ? t("setup.adding") : t("setup.add")}
                      </Button>
                    </li>
                  {:else}
                    <li class="rounded-nav border border-dashed border-border px-3 py-4 text-center text-caption text-muted-foreground">
                      {loadingRec
                        ? t("setup.recLoading")
                        : recLoaded
                          ? t("setup.recEmpty")
                          : t("common.loading")}
                    </li>
                  {/each}
                </ul>

                {#if pool.length > REC_PAGE}
                  <button
                    type="button"
                    class="flex h-8 w-full items-center justify-center gap-2 rounded-control border border-border bg-surface-subtle text-caption text-muted-foreground hover:bg-rule"
                    onclick={() => (batch += 1)}
                  >
                    <RefreshCwIcon class="size-3.5" aria-hidden="true" />
                    {t("setup.shuffle")}
                  </button>
                {/if}
              {/if}
              {#if recNote}<Note tone={recNote.tone}>{recNote.text}</Note>{/if}
            </div>
          {:else}
            <div class="flex flex-col gap-3">
              <Field label={t("setup.manualLabel")} for="setup-manual" hint={t("setup.manualHint")}>
                <Input
                  id="setup-manual"
                  placeholder={t("setup.manualPlaceholder")}
                  bind:value={link}
                />
              </Field>
              <div class="flex flex-wrap items-center gap-3">
                <Button size="lg" disabled={adding || link.trim() === ""} onclick={() => void addManual()}>
                  {adding ? t("setup.adding") : t("setup.add")}
                </Button>
              </div>
              {#if manualNote}<Note tone={manualNote.tone}>{manualNote.text}</Note>{/if}
            </div>
          {/if}

          {#if sources.length > 0}
            <div class="flex flex-col gap-2">
              <p class="text-caption text-muted-foreground">
                {t("setup.addedCount", { n: sources.length })}
              </p>
              <div class="flex flex-wrap gap-2">
                {#each sources as row (row.id)}
                  <span
                    class="inline-flex h-7 items-center gap-1.5 rounded-chip bg-primary-soft px-2.5 text-caption text-primary"
                  >
                    {handleOf(row)}
                    <button
                      type="button"
                      class="ui-transition text-caption disabled:opacity-50"
                      aria-label={t("setup.removeSource", { handle: handleOf(row) })}
                      disabled={removingId !== null}
                      onclick={() => void removeSource(row)}
                    >
                      ✕
                    </button>
                  </span>
                {/each}
              </div>
            </div>
          {/if}
        </section>
      {/if}
    </div>
  </div>

  <div class="flex items-start gap-2.5 rounded-nav bg-primary-surface px-3.5 py-3">
    <LightbulbIcon class="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
    <p class="text-caption text-muted-foreground">
      <span class="font-medium text-foreground">{t("setup.tipTitle")}</span>
      {t("setup.tipBody")}
    </p>
  </div>

  <div class="flex items-center gap-3">
    <Button variant="outline" size="lg" disabled={step === 1} onclick={goPrev}>
      {t("setup.prev")}
    </Button>
    <span class="flex-1 text-caption text-muted-foreground">{gateNote}</span>
    <Button size="lg" disabled={nextDisabled} onclick={() => void goNext()}>
      {step === 3 ? t("setup.nextEnter") : t("setup.next")}
    </Button>
  </div>
</div>
