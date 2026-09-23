<script lang="ts">
  /** 初始化向导（FR-OPS-02）：两件事——密钥与登录。步骤编号在此是真序列，故允许编号。
   *
   * 密钥写入 config.yaml（不入库），凭据类字段重启才生效；登录会话落 sessions/。
   */
  import { api, errorText } from "$lib/api/client";
  import type { SetupSecretsPayload } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { session } from "$lib/stores/session.svelte";
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

  let apiId = $state("");
  let apiHash = $state("");
  let botToken = $state("");
  let useProxy = $state(false);
  let proxyScheme = $state("socks5");
  let proxyHost = $state("");
  let proxyPort = $state("1080");

  let phone = $state("");
  let code = $state("");
  let password = $state("");
  let codeHash = $state("");

  let busy = $state(false);
  let notice = $state("");
  let error = $state("");

  const status = $derived(session.setup);
  const keysReady = $derived(Boolean(status?.has_api_id && status?.has_api_hash));
  const done = $derived(Boolean(status?.connected));

  async function saveSecrets() {
    busy = true;
    error = "";
    notice = "";
    try {
      const payload: SetupSecretsPayload = {};
      if (apiId.trim()) payload.api_id = Number(apiId.trim());
      if (apiHash.trim()) payload.api_hash = apiHash.trim();
      if (botToken.trim()) payload.bot_token = botToken.trim();
      if (useProxy) {
        payload.proxy = {
          scheme: proxyScheme,
          hostname: proxyHost.trim(),
          port: Number(proxyPort.trim()) || 1080,
        };
      }
      await api.post("/api/setup/secrets", payload);
      await session.loadSetup();
      notice = t("setup.savedRestart");
    } catch (err) {
      error = errorText(err, t("common.error"));
    } finally {
      busy = false;
    }
  }

  async function sendCode() {
    busy = true;
    error = "";
    notice = "";
    try {
      const resp = await api.post<{ code_hash: string }>("/api/auth/telegram/send-code", {
        phone: phone.trim(),
      });
      codeHash = resp.code_hash;
      notice = t("setup.codeSent");
    } catch (err) {
      error = errorText(err, t("common.error"));
    } finally {
      busy = false;
    }
  }

  async function signIn() {
    busy = true;
    error = "";
    try {
      await api.post("/api/auth/telegram/sign-in", {
        phone: phone.trim(),
        code: code.trim(),
        code_hash: codeHash,
        password: password.trim() || undefined,
      });
      await Promise.all([session.loadSetup(), session.loadMe()]);
      navigate(pathOf("dashboard"));
    } catch (err) {
      error = errorText(err, t("common.error"));
    } finally {
      busy = false;
    }
  }
</script>

<div class="mx-auto flex min-h-dvh w-full max-w-[560px] flex-col gap-6 px-5 py-10">
  <header class="border-b border-rule pb-3">
    <p class="tabular text-micro text-muted-foreground">{t("app.repo")}</p>
    <h1 class="mt-1 text-title font-semibold">{t("setup.title")}</h1>
    <p class="mt-1 text-small text-muted-foreground">{t("setup.lede")}</p>
  </header>

  <section>
    <h2 class="text-micro text-muted-foreground">{t("setup.checklist")}</h2>
    <ul class="mt-2 divide-y divide-rule border-y border-rule">
      <li class="flex items-center justify-between py-2">
        <span class="text-small">api_id</span>
        <Lamp
          tone={status?.has_api_id ? "done" : "fail"}
          label={status?.has_api_id ? t("setup.ready") : t("setup.missing")}
        />
      </li>
      <li class="flex items-center justify-between py-2">
        <span class="text-small">api_hash</span>
        <Lamp
          tone={status?.has_api_hash ? "done" : "fail"}
          label={status?.has_api_hash ? t("setup.ready") : t("setup.missing")}
        />
      </li>
      <li class="flex items-center justify-between py-2">
        <span class="text-small">bot_token</span>
        <Lamp
          tone={status?.has_bot_token ? "done" : "idle"}
          label={status?.has_bot_token ? t("setup.ready") : t("setup.optionalMissing")}
        />
      </li>
      <li class="flex items-center justify-between py-2">
        <span class="text-small">{t("setup.proxy")}</span>
        <Lamp
          tone={status?.proxy ? "done" : "idle"}
          label={status?.proxy ? t("setup.proxyOn") : t("setup.proxyOff")}
        />
      </li>
      <li class="flex items-center justify-between py-2">
        <span class="text-small">{t("setup.connection")}</span>
        <Lamp
          tone={status?.connected ? "done" : "fail"}
          label={status?.connected ? t("app.connected") : t("app.disconnected")}
        />
      </li>
    </ul>
  </section>

  {#if done}
    <section class="flex flex-col gap-3">
      <h2 class="text-body font-medium">{t("setup.doneTitle")}</h2>
      <p class="text-small text-muted-foreground">{t("setup.doneHint")}</p>
      <Button class="self-start" onclick={() => navigate(pathOf("dashboard"))}>
        {t("setup.enter")}
      </Button>
    </section>
  {:else}
    <section class="flex flex-col gap-4 border-t border-rule pt-4">
      <div>
        <h2 class="text-body font-medium">{t("setup.step1")}</h2>
        <p class="mt-1 text-small text-muted-foreground">{t("setup.step1Hint")}</p>
      </div>

      <Field label={t("setup.apiId")} for="setup-api-id">
        <Input id="setup-api-id" class="tabular" bind:value={apiId} />
      </Field>
      <Field label={t("setup.apiHash")} for="setup-api-hash">
        <Input id="setup-api-hash" class="tabular" bind:value={apiHash} />
      </Field>
      <Field label={`${t("setup.botToken")}（${t("setup.optional")}）`} for="setup-bot-token">
        <Input id="setup-bot-token" class="tabular" type="password" bind:value={botToken} />
      </Field>

      <div class="flex flex-col gap-3">
        <label class="flex items-center gap-2 text-small">
          <Checkbox bind:checked={useProxy} />
          {t("setup.proxy")}
        </label>
        {#if useProxy}
          <p class="text-micro text-muted-foreground">{t("setup.proxyHint")}</p>
          <div class="grid grid-cols-3 gap-2">
            <Field label={t("setup.proxyScheme")}>
              <Select type="single" bind:value={proxyScheme}>
                <SelectTrigger class="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="socks5">socks5</SelectItem>
                  <SelectItem value="http">http</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label={t("setup.proxyHost")} for="setup-proxy-host">
              <Input id="setup-proxy-host" class="tabular" bind:value={proxyHost} />
            </Field>
            <Field label={t("setup.proxyPort")} for="setup-proxy-port">
              <Input id="setup-proxy-port" class="tabular" bind:value={proxyPort} />
            </Field>
          </div>
        {/if}
      </div>

      <div class="flex items-center gap-2">
        <Button disabled={busy} onclick={() => void saveSecrets()}>
          {busy ? t("setup.saving") : t("setup.save")}
        </Button>
        {#if notice}<Note tone="done">{notice}</Note>{/if}
      </div>
      {#if error}<Note tone="fail">{error}</Note>{/if}
    </section>

    <section class="flex flex-col gap-4 border-t border-rule pt-4">
      <div>
        <h2 class="text-body font-medium">{t("setup.step2")}</h2>
        <p class="mt-1 text-small text-muted-foreground">{t("setup.step2Hint")}</p>
      </div>

      {#if !keysReady}
        <Note tone="wait">{t("setup.step1Hint")}</Note>
      {:else}
        <Field label={t("setup.phone")} for="setup-phone">
          <div class="flex gap-2">
            <Input
              id="setup-phone"
              class="tabular flex-1"
              bind:value={phone}
              placeholder={t("setup.phonePlaceholder")}
            />
            <Button
              variant="outline"
              disabled={busy || phone.trim().length === 0}
              onclick={() => void sendCode()}
            >
              {busy ? t("setup.sending") : t("setup.sendCode")}
            </Button>
          </div>
        </Field>

        {#if codeHash}
          <Field label={t("setup.code")} for="setup-code">
            <Input id="setup-code" class="tabular" bind:value={code} />
          </Field>
          <Field
            label={`${t("setup.password2fa")}（${t("setup.optional")}）`}
            for="setup-password"
          >
            <Input id="setup-password" type="password" bind:value={password} />
          </Field>
          <Button
            class="self-start"
            disabled={busy || code.trim().length === 0}
            onclick={() => void signIn()}
          >
            {busy ? t("setup.signingIn") : t("setup.signIn")}
          </Button>
        {/if}
      {/if}
    </section>
  {/if}
</div>
