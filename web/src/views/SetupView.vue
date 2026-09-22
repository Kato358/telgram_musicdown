<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api, ApiError } from "@/api/client";

const { t } = useI18n();

interface SetupStatus {
  complete: boolean;
  has_api_id: boolean;
  has_api_hash: boolean;
  has_bot_token: boolean;
  proxy: boolean;
  connected: boolean;
}

const status = ref<SetupStatus | null>(null);

// 步骤 1：密钥
const apiId = ref("");
const apiHash = ref("");
const botToken = ref("");
// 代理
const useProxy = ref(false);
const proxyScheme = ref("socks5");
const proxyHost = ref("");
const proxyPort = ref("1080");
// 步骤 2：登录
const phone = ref("");
const code = ref("");
const password = ref("");
const codeHash = ref("");
const step = ref<"secrets" | "login" | "done">("secrets");
const busy = ref(false);
const message = ref("");
const error = ref("");

async function load() {
  status.value = await api.get<SetupStatus>("/api/setup/status");
  if (status.value.connected) step.value = "done";
  else if (status.value.has_api_id && status.value.has_api_hash) step.value = "login";
}

async function saveSecrets() {
  busy.value = true;
  error.value = "";
  try {
    await api.post("/api/setup/secrets", {
      api_id: apiId.value ? Number(apiId.value) : undefined,
      api_hash: apiHash.value || undefined,
      bot_token: botToken.value || undefined,
      proxy: useProxy.value
        ? {
            scheme: proxyScheme.value,
            hostname: proxyHost.value,
            port: Number(proxyPort.value) || 1080,
          }
        : undefined,
    });
    message.value = t("setup.savedRestart");
    await load();
    if (status.value?.has_api_id && status.value?.has_api_hash) step.value = "login";
  } catch (e) {
    error.value = e instanceof Error ? e.message : t("common.error");
  } finally {
    busy.value = false;
  }
}

async function sendCode() {
  busy.value = true;
  error.value = "";
  try {
    const resp = await api.post<{ code_hash: string }>("/api/auth/telegram/send-code", {
      phone: phone.value,
    });
    codeHash.value = resp.code_hash;
    message.value = t("setup.codeSent");
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : t("common.error");
  } finally {
    busy.value = false;
  }
}

async function signIn() {
  busy.value = true;
  error.value = "";
  try {
    await api.post("/api/auth/telegram/sign-in", {
      phone: phone.value,
      code: code.value,
      code_hash: codeHash.value,
      password: password.value || undefined,
    });
    step.value = "done";
    await load();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : t("common.error");
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div>
    <h1 class="mb-4 text-[17px] font-semibold text-hi">{{ t("setup.title") }}</h1>

    <div v-if="step === 'done'" class="rounded bg-raise px-4 py-3 text-[13px] text-signal">
      {{ t("setup.done") }}
    </div>

    <!-- 步骤 1：API 密钥 + 代理 -->
    <section v-if="step !== 'done'" class="mb-6 flex flex-col gap-4 rounded bg-raise p-4">
      <h2 class="text-[14px] font-medium text-hi">{{ t("setup.stepSecrets") }}</h2>
      <label class="flex flex-col gap-1.5">
        <span class="text-[13px] text-mid">api_id</span>
        <input v-model="apiId" type="text" class="rounded bg-inset px-3 py-2 font-mono text-[13px] text-hi" />
      </label>
      <label class="flex flex-col gap-1.5">
        <span class="text-[13px] text-mid">api_hash</span>
        <input v-model="apiHash" type="text" class="rounded bg-inset px-3 py-2 font-mono text-[13px] text-hi" />
      </label>
      <label class="flex flex-col gap-1.5">
        <span class="text-[13px] text-mid">bot_token ({{ t("setup.optional") }})</span>
        <input v-model="botToken" type="password" class="rounded bg-inset px-3 py-2 font-mono text-[13px] text-hi" />
      </label>

      <div class="flex flex-col gap-2">
        <label class="flex items-center gap-2 text-[13px] text-mid">
          <input v-model="useProxy" type="checkbox" />
          {{ t("setup.useProxy") }}
        </label>
        <div v-if="useProxy" class="grid gap-2 md:grid-cols-3">
          <select v-model="proxyScheme" class="rounded bg-inset px-3 py-2 text-[13px] text-hi">
            <option value="socks5">socks5</option>
            <option value="http">http</option>
          </select>
          <input v-model="proxyHost" type="text" :placeholder="t('setup.proxyHost')" class="rounded bg-inset px-3 py-2 text-[13px] text-hi" />
          <input v-model="proxyPort" type="text" :placeholder="t('setup.proxyPort')" class="rounded bg-inset px-3 py-2 text-[13px] text-hi" />
        </div>
      </div>

      <button
        class="self-start rounded bg-accent px-4 py-2 text-[13px] font-medium text-base"
        :disabled="busy"
        @click="saveSecrets"
      >
        {{ t("setup.saveSecrets") }}
      </button>
      <span v-if="message" class="text-[13px] text-signal">{{ message }}</span>
      <span v-if="error" class="text-[13px] text-fail">{{ error }}</span>
    </section>

    <!-- 步骤 2：Telegram 登录 -->
    <section v-if="step === 'login' || (step === 'secrets' && status?.has_api_id && status?.has_api_hash)"
      class="mb-6 flex flex-col gap-4 rounded bg-raise p-4">
      <h2 class="text-[14px] font-medium text-hi">{{ t("setup.stepLogin") }}</h2>
      <label class="flex flex-col gap-1.5">
        <span class="text-[13px] text-mid">{{ t("setup.phone") }}</span>
        <div class="flex gap-2">
          <input v-model="phone" type="text" placeholder="+86..." class="flex-1 rounded bg-inset px-3 py-2 text-[13px] text-hi" />
          <button class="rounded bg-accent px-3 py-2 text-[13px] font-medium text-base" :disabled="busy || !phone" @click="sendCode">
            {{ t("setup.sendCode") }}
          </button>
        </div>
      </label>
      <template v-if="codeHash">
        <label class="flex flex-col gap-1.5">
          <span class="text-[13px] text-mid">{{ t("setup.code") }}</span>
          <input v-model="code" type="text" class="rounded bg-inset px-3 py-2 text-[13px] text-hi" />
        </label>
        <label class="flex flex-col gap-1.5">
          <span class="text-[13px] text-mid">{{ t("setup.password2fa") }} ({{ t("setup.optional") }})</span>
          <input v-model="password" type="password" class="rounded bg-inset px-3 py-2 text-[13px] text-hi" />
        </label>
        <button
          class="self-start rounded bg-accent px-4 py-2 text-[13px] font-medium text-base"
          :disabled="busy || !code"
          @click="signIn"
        >
          {{ t("setup.signIn") }}
        </button>
      </template>
      <span v-if="error" class="text-[13px] text-fail">{{ error }}</span>
    </section>

    <p class="text-[12px] text-mid">{{ t("setup.hint") }}</p>
  </div>
</template>
