<script lang="ts">
  /** Web 控制台登录（FR-WEB-02）。
   *
   * 只在「设了 web_login_secret（或绑定非本机地址）」的部署出现：此时后端对所有
   * `/api/*` 校验会话 cookie，没有 cookie 就恒 401——本页是唯一的进门方式。
   * 本机免密部署不渲染本页（`session.needsWebLogin` 为 false）。
   */
  import { t } from "$lib/i18n/index.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { errorText } from "$lib/api/client";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import Field from "$lib/components/app/Field.svelte";

  let secret = $state("");
  let busy = $state(false);
  let error = $state("");

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (busy || !secret) return;
    busy = true;
    error = "";
    try {
      await session.webLogin(secret);
      secret = "";
    } catch (err) {
      error = errorText(err, t("login.failed"));
    } finally {
      busy = false;
    }
  }
</script>

<div class="grid min-h-dvh place-items-center bg-background px-4 py-10">
  <div class="w-full max-w-sm">
    <div class="mb-6 flex flex-col items-center gap-1 text-center">
      <h1 class="text-h1 font-semibold">{t("app.name")}</h1>
      <p class="text-caption text-muted-foreground">{t("login.lede")}</p>
    </div>

    <form class="card flex flex-col gap-4 p-5" onsubmit={submit}>
      <Field label={t("login.secret")} for="web-login-secret" hint={t("login.secretHint")}>
        <Input
          id="web-login-secret"
          type="password"
          autocomplete="current-password"
          bind:value={secret}
          disabled={busy}
          autofocus
        />
      </Field>

      {#if error}
        <p class="text-caption text-destructive-text" role="alert">{error}</p>
      {/if}

      <Button type="submit" disabled={busy || !secret}>
        {busy ? t("login.submitting") : t("login.submit")}
      </Button>
    </form>

    <p class="mt-4 text-center text-caption text-faint-foreground">{t("login.where")}</p>
  </div>
</div>
