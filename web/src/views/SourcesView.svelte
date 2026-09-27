<script lang="ts">
  /** 音乐源：账号已加入、可搜索与下载的频道（FR-SRC-01/02/03）。
   *
   * 列表是服务端事实源：增删后整表重取；开关只发变化的那一个字段，并用 PUT 返回的行覆盖本地，
   * 不做乐观翻转——失败时开关停在服务端真值上，旁边给一条失败提示。
   * 添加源只加源：不做任何拉取（同步子系统已移除），入库由搜索页/链接入队按需触发。
   *
   * 两段内容各自成卡：上面「TG 音乐源」= 频道源（统计卡 + 添加 + 列表），下面「在线音乐源」=
   * 网易云 / QQ 音乐 / 酷狗（`/api/sources/online`，逐平台启停，搜索页那三颗药丸的勾选值就是
   * 这些行的 id——scope 同一套）。**TG 那一段只在逐源模式在场**：全账号模式下频道源不参与搜索，
   * 整段收起（配置保留），页面只剩在线源——那才是两种模式下都在跑的东西。
   */
  import { onMount } from "svelte";
  import { api, errorText } from "$lib/api/client";
  import type { OnlineSourceRow, OnlineSourcesResponse, SourceRow } from "$lib/api/types";
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import { formatCount } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { session } from "$lib/stores/session.svelte";
  import type { Tone } from "$lib/tone";
  import { Button } from "$lib/components/ui/button";
  import ConfirmDialog from "$lib/components/app/ConfirmDialog.svelte";
  import { Input } from "$lib/components/ui/input";
  import SourceCard from "$lib/components/app/SourceCard.svelte";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";
  import SectionCard from "$lib/components/app/SectionCard.svelte";
  import StatCard from "$lib/components/app/StatCard.svelte";

  let rows = $state<SourceRow[]>([]);
  let loaded = $state(false);
  let listError = $state("");

  /** 在线源三行（顺序由服务端定，前端不排序也不补行）；`has_key` 只决定要不要提示配 Key。 */
  let online = $state<OnlineSourcesResponse | null>(null);
  let onlineError = $state("");

  let link = $state("");
  let adding = $state(false);
  let addError = $state("");

  /** 源 id → 行内反馈（成功/失败都只留一条）。 */
  let notes = $state<Record<number, { tone: Tone; text: string }>>({});

  /** 开关没有 bind 时 bits-ui 会把点击结果留在本地覆盖值里：PUT 失败要重挂载，才能回到服务端真值。 */
  let switchEpoch = $state(0);

  let removeOpen = $state(false);
  let removeTarget = $state<SourceRow | null>(null);
  let removeHistory = $state(false);
  let removing = $state(false);
  let removeError = $state("");

  /** 统计卡的数字就地从已拉到的源列表里算：同一份事实不为统计再打一次接口。 */
  const enabledCount = $derived(rows.filter((row) => row.enabled).length);

  /** 频道句柄：后端存的是不含 @ 的 username。 */
  function handleOf(row: SourceRow): string | null {
    if (!row.username) return null;
    return row.username.startsWith("@") ? row.username : `@${row.username}`;
  }

  /** 标题悬停的完整身份：卡面只有 160px，长名与 chat id 收进提示里。 */
  function identityOf(row: SourceRow): string {
    return [row.title, handleOf(row), row.telegram_chat_id].filter(Boolean).join(" · ");
  }

  function setNote(id: number, tone: Tone, text: string) {
    notes = { ...notes, [id]: { tone, text } };
  }

  function clearNote(id: number) {
    const next = { ...notes };
    delete next[id];
    notes = next;
  }

  async function load() {
    try {
      rows = await api.get<SourceRow[]>("/api/sources");
      listError = "";
    } catch (err) {
      listError = errorText(err, t("common.error"));
    } finally {
      loaded = true;
    }
  }

  /** 在线源与频道分开拉：这一趟挂了只让那一段说，不挡已加载的频道列表。 */
  async function loadOnline() {
    try {
      online = await api.get<OnlineSourcesResponse>("/api/sources/online");
      onlineError = "";
    } catch (err) {
      onlineError = errorText(err, t("common.error"));
    }
  }

  async function add() {
    const value = link.trim();
    if (value.length === 0 || adding) return;
    adding = true;
    addError = "";
    try {
      await api.post<SourceRow>("/api/sources", { link: value });
      link = "";
      await load();
    } catch (err) {
      addError = errorText(err, t("sources.unreachable"));
    } finally {
      adding = false;
    }
  }

  /** 只发变化的那一个字段，用返回的行替换本地行。 */
  async function patch(row: SourceRow, body: { enabled?: boolean }) {
    clearNote(row.id);
    try {
      const updated = await api.put<SourceRow>(`/api/sources/${row.id}`, body);
      rows = rows.map((current) => (current.id === updated.id ? updated : current));
    } catch (err) {
      switchEpoch += 1;
      setNote(row.id, "fail", errorText(err, t("common.error")));
    }
  }

  /** 在线平台同款写法（行内提示与重挂载共用频道那套机制）：id 是负号 scope，
   *  与频道 id 不会撞车，所以 notes 与 switchEpoch 可以共用。 */
  async function patchOnline(row: OnlineSourceRow, enabled: boolean) {
    const current = online;
    if (current === null) return;
    clearNote(row.id);
    try {
      const updated = await api.put<OnlineSourceRow>(`/api/sources/online/${row.provider}`, {
        enabled,
      });
      online = {
        ...current,
        providers: current.providers.map((item) => (item.id === updated.id ? updated : item)),
      };
    } catch (err) {
      switchEpoch += 1;
      setNote(row.id, "fail", errorText(err, t("common.error")));
    }
  }

  function openRemove(row: SourceRow) {
    removeTarget = row;
    removeHistory = false;
    removeError = "";
    removeOpen = true;
  }

  /** 确认成功走这里（清目标 + 关门）；用户按取消 / Esc / 点遮罩则由弹窗的 onclosed 清目标。 */
  function closeRemove() {
    removeOpen = false;
    removeTarget = null;
  }

  async function confirmRemove() {
    const target = removeTarget;
    if (target === null || removing) return;
    removing = true;
    removeError = "";
    try {
      await api.delete<{ ok: boolean }>(
        `/api/sources/${target.id}?with_history=${String(removeHistory)}`,
      );
      closeRemove();
      await load();
    } catch (err) {
      removeError = errorText(err, t("common.error"));
    } finally {
      removing = false;
    }
  }

  onMount(() => {
    void load();
    void loadOnline();
  });
</script>

<PageHeader
  title={t("sources.title")}
  lede={session.globalSearch ? t("sources.ledeGlobal") : t("sources.lede")}
>
  {#snippet aside()}
    {#if !session.globalSearch}
      <span class="tabular text-caption text-muted-foreground">
        {t("sources.enabledCount", { on: enabledCount, total: rows.length })}
      </span>
    {/if}
  {/snippet}
</PageHeader>

{#if session.globalSearch}
  <Note>
    <span class="block">{t("sources.globalModeNotice")}</span>
    <Button
      class="mt-2"
      variant="outline"
      size="sm"
      onclick={() => navigate(pathOf("settings"))}
    >
      {t("sources.globalModeBack")}
    </Button>
  </Note>
{/if}

{#if !session.globalSearch}
  <div class="grid grid-cols-2 gap-4">
    <StatCard
      label={t("sources.statTotal")}
      value={formatCount(rows.length)}
      hint={t("sources.statTotalHint")}
      tone="amber"
      icon={RadioTowerIcon}
    />
    <StatCard
      label={t("sources.statEnabled")}
      value={formatCount(enabledCount)}
      hint={t("sources.statEnabledHint")}
      tone="primary"
      icon={CircleCheckIcon}
    />
  </div>

  <SectionCard title={t("sources.tgSection")} hint={t("sources.tgHint")}>
    <div class="flex flex-col gap-4">
      <div>
        <form
          class="flex flex-wrap items-center gap-3"
          onsubmit={(event) => {
            event.preventDefault();
            void add();
          }}
        >
          <div class="min-w-52 flex-1">
            <Input
              bind:value={link}
              aria-label={t("sources.linkLabel")}
              placeholder={t("sources.linkPlaceholder")}
            />
          </div>
          <Button type="submit" size="lg" disabled={adding || link.trim().length === 0}>
            {adding ? t("sources.adding") : t("sources.add")}
          </Button>
        </form>
        <p class="mt-2 text-caption text-muted-foreground">{t("sources.addHint")}</p>
      </div>

      {#if addError}
        <Note tone="fail">{addError}</Note>
      {/if}

      {#if listError}
        <Note tone="fail">{listError}</Note>
      {/if}

      {#if rows.length === 0}
        {#if !loaded}
          <p class="text-caption text-muted-foreground">{t("common.loading")}</p>
        {:else if !listError}
          <EmptyState title={t("sources.empty")} />
        {/if}
      {:else}
        <ul
          class="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] items-start gap-2.5 max-[640px]:grid-cols-1"
        >
          {#each rows as row (row.id)}
            <SourceCard
              switchId={`source-enabled-${row.id}`}
              title={row.title}
              meta={handleOf(row) ?? String(row.telegram_chat_id)}
              mono
              tooltip={identityOf(row)}
              enabled={row.enabled}
              error={notes[row.id]?.text ?? null}
              removable
              epoch={switchEpoch}
              onToggle={(checked) => void patch(row, { enabled: checked })}
              onRemove={() => openRemove(row)}
            />
          {/each}
        </ul>
      {/if}
    </div>
  </SectionCard>
{/if}

<SectionCard title={t("sources.onlineSection")} hint={t("sources.onlineHint")}>
  <div class="flex flex-col gap-4">
    {#if onlineError}
      <Note tone="fail">{onlineError}</Note>
    {/if}

    {#if online && !online.has_key}
      <!-- 没 Key 时开关照样拨得动（后端会落库），但拨完搜不到东西：先把原因和去处说清 -->
      <Note tone="wait">
        <span class="block">{t("sources.onlineKeyMissing")}</span>
        <Button
          class="mt-2"
          variant="outline"
          size="sm"
          onclick={() => navigate(pathOf("settings"))}
        >
          {t("sources.onlineKeyAction")}
        </Button>
      </Note>
    {/if}

    {#if online}
      <ul
        class="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] items-start gap-2.5 max-[640px]:grid-cols-1"
      >
        {#each online.providers as row (row.id)}
          <!-- 在线平台是内建的：只启停，没有 × -->
          <SourceCard
            switchId={`online-enabled-${row.provider}`}
            title={row.title}
            meta={t("sources.onlineBy")}
            enabled={row.enabled}
            error={notes[row.id]?.text ?? null}
            epoch={switchEpoch}
            onToggle={(checked) => void patchOnline(row, checked)}
          />
        {/each}
      </ul>
    {:else if !onlineError}
      <p class="text-caption text-muted-foreground">{t("common.loading")}</p>
    {/if}
  </div>
</SectionCard>

<ConfirmDialog
  bind:open={removeOpen}
  title={t("sources.removeTitle")}
  description={t("sources.removeBody")}
  target={removeTarget ? { name: removeTarget.title, meta: handleOf(removeTarget) } : null}
  option={{ label: t("sources.removeHistory"), hint: t("sources.removeHistoryHint") }}
  bind:optionChecked={removeHistory}
  error={removeError || null}
  confirmLabel={t("sources.removeConfirm")}
  pendingLabel={t("sources.removePending")}
  pending={removing}
  onconfirm={() => void confirmRemove()}
  onclosed={() => (removeTarget = null)}
/>
