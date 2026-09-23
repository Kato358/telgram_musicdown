<script lang="ts">
  /** 音乐源：账号已加入、可搜索与下载的频道（FR-SRC-01/02/03）。
   *
   * 列表是服务端事实源：增删后整表重取；开关只发变化的那一个字段，并用 PUT 返回的行覆盖本地，
   * 不做乐观翻转——失败时开关停在服务端真值上，旁边给一条失败提示。
   */
  import { onMount } from "svelte";
  import { api, errorText } from "$lib/api/client";
  import type { SourceRow } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
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
  import { Label } from "$lib/components/ui/label";
  import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
  } from "$lib/components/ui/select";
  import { Switch } from "$lib/components/ui/switch";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import Field from "$lib/components/app/Field.svelte";
  import Lamp from "$lib/components/app/Lamp.svelte";
  import Note from "$lib/components/app/Note.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

  type Direction = "backward" | "forward";

  let rows = $state<SourceRow[]>([]);
  let loaded = $state(false);
  let listError = $state("");

  let link = $state("");
  let adding = $state(false);
  let addError = $state("");

  /** 源 id → 行内反馈（成功/失败都只留一条）。 */
  let notes = $state<Record<number, { tone: Tone; text: string }>>({});

  /** 开关没有 bind 时 bits-ui 会把点击结果留在本地覆盖值里：PUT 失败要重挂载，才能回到服务端真值。 */
  let syncEpoch = $state(0);

  let backfillOpen = $state(false);
  let backfillTarget = $state<SourceRow | null>(null);
  let direction = $state<Direction>("backward");
  let limitText = $state("");
  let backfilling = $state(false);
  let backfillError = $state("");

  let removeOpen = $state(false);
  let removeTarget = $state<SourceRow | null>(null);
  let removeHistory = $state(false);
  let removing = $state(false);
  let removeError = $state("");

  const enabledCount = $derived(rows.filter((row) => row.enabled).length);

  /** 触发器关闭时 bits-ui 不渲染选项，标签只能由 items 提供。 */
  const directionItems = $derived([
    { value: "backward", label: t("sources.backward") },
    { value: "forward", label: t("sources.forward") },
  ]);

  /** 频道句柄：后端存的是不含 @ 的 username。 */
  function handleOf(row: SourceRow): string | null {
    if (!row.username) return null;
    return row.username.startsWith("@") ? row.username : `@${row.username}`;
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
  async function patch(row: SourceRow, body: { enabled?: boolean; auto_sync?: boolean }) {
    clearNote(row.id);
    try {
      const updated = await api.put<SourceRow>(`/api/sources/${row.id}`, body);
      rows = rows.map((current) => (current.id === updated.id ? updated : current));
    } catch (err) {
      syncEpoch += 1;
      setNote(row.id, "fail", errorText(err, t("common.error")));
    }
  }

  function openBackfill(row: SourceRow) {
    backfillTarget = row;
    direction = "backward";
    limitText = "";
    backfillError = "";
    backfillOpen = true;
  }

  function closeBackfill() {
    backfillOpen = false;
    backfillTarget = null;
  }

  function setDirection(value: string) {
    direction = value === "forward" ? "forward" : "backward";
  }

  async function startBackfill() {
    const target = backfillTarget;
    if (target === null || backfilling) return;
    const raw = limitText.trim();
    const parsed = raw.length === 0 ? null : Number(raw);
    const limit =
      parsed !== null && Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : null;
    backfilling = true;
    backfillError = "";
    try {
      await api.post<{ task_id: number }>(`/api/sources/${target.id}/backfill`, {
        direction,
        limit,
      });
      closeBackfill();
      setNote(target.id, "done", t("sources.started"));
      await load();
    } catch (err) {
      backfillError = errorText(err, t("common.error"));
    } finally {
      backfilling = false;
    }
  }

  function openRemove(row: SourceRow) {
    removeTarget = row;
    removeHistory = false;
    removeError = "";
    removeOpen = true;
  }

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
  });
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("sources.title")} lede={t("sources.lede")}>
    {#snippet aside()}
      <span class="tabular text-micro text-muted-foreground">
        {t("sources.enabledCount", { on: enabledCount, total: rows.length })}
      </span>
    {/snippet}
  </PageHeader>

  <form
    class="flex flex-wrap items-center gap-2"
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
    <Button type="submit" disabled={adding || link.trim().length === 0}>
      {adding ? t("sources.adding") : t("sources.add")}
    </Button>
  </form>

  {#if addError}
    <Note tone="fail">{addError}</Note>
  {/if}

  {#if listError}
    <Note tone="fail">{listError}</Note>
  {/if}

  {#if rows.length === 0}
    {#if !loaded}
      <p class="text-micro text-muted-foreground">{t("common.loading")}</p>
    {:else if !listError}
      <EmptyState title={t("sources.empty")} />
    {/if}
  {:else}
    <ul class="divide-y divide-rule">
      {#each rows as row (row.id)}
        {@const handle = handleOf(row)}
        <li class="flex flex-wrap items-center gap-3 py-3">
          <Lamp tone={row.enabled ? "done" : "idle"} />

          <div class="min-w-0 flex-1">
            <p class="truncate text-small font-medium">{row.title}</p>
            <p class="tabular truncate text-micro text-muted-foreground">
              {handle ? `${handle} | ${row.telegram_chat_id}` : row.telegram_chat_id}
            </p>
          </div>

          {#key syncEpoch}
            <div class="flex items-center gap-2">
              <Label for={`source-enabled-${row.id}`} class="text-micro text-muted-foreground">
                {t("sources.enabled")}
              </Label>
              <Switch
                id={`source-enabled-${row.id}`}
                checked={row.enabled}
                aria-label={t("sources.enabled")}
                onCheckedChange={(checked) => void patch(row, { enabled: checked })}
              />
            </div>

            <div class="flex items-center gap-2">
              <Label for={`source-autosync-${row.id}`} class="text-micro text-muted-foreground">
                {t("sources.autoSync")}
              </Label>
              <Switch
                id={`source-autosync-${row.id}`}
                checked={row.auto_sync}
                aria-label={t("sources.autoSync")}
                onCheckedChange={(checked) => void patch(row, { auto_sync: checked })}
              />
            </div>
          {/key}

          <div class="flex shrink-0 items-center gap-1">
            <Button variant="ghost" size="xs" onclick={() => openBackfill(row)}>
              {t("sources.backfill")}
            </Button>
            <Button variant="destructive" size="xs" onclick={() => openRemove(row)}>
              {t("sources.remove")}
            </Button>
          </div>

          {#if notes[row.id]}
            <Note tone={notes[row.id].tone} class="basis-full">{notes[row.id].text}</Note>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<Dialog
  bind:open={backfillOpen}
  onOpenChange={(open) => {
    if (!open) backfillTarget = null;
  }}
>
  <DialogContent>
    {#if backfillTarget}
      <DialogHeader>
        <DialogTitle>{t("sources.backfillTitle", { title: backfillTarget.title })}</DialogTitle>
        <DialogDescription>{t("sources.backfillHint")}</DialogDescription>
      </DialogHeader>

      <div class="flex flex-col gap-4">
        <Field label={t("sources.direction")} for="backfill-direction">
          <Select
            type="single"
            value={direction}
            items={directionItems}
            onValueChange={setDirection}
          >
            <SelectTrigger id="backfill-direction" class="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="backward">{t("sources.backward")}</SelectItem>
              <SelectItem value="forward">{t("sources.forward")}</SelectItem>
            </SelectContent>
          </Select>
        </Field>

        <Field label={t("sources.limit")} for="backfill-limit" hint={t("sources.limitNone")}>
          <Input
            id="backfill-limit"
            type="number"
            min="1"
            class="tabular w-32"
            bind:value={limitText}
          />
        </Field>
      </div>

      {#if backfillError}
        <Note tone="fail">{backfillError}</Note>
      {/if}

      <DialogFooter>
        <Button variant="outline" onclick={closeBackfill}>{t("common.cancel")}</Button>
        <Button disabled={backfilling} onclick={() => void startBackfill()}>
          {t("sources.start")}
        </Button>
      </DialogFooter>
    {/if}
  </DialogContent>
</Dialog>

<Dialog
  bind:open={removeOpen}
  onOpenChange={(open) => {
    if (!open) removeTarget = null;
  }}
>
  <DialogContent>
    {#if removeTarget}
      <DialogHeader>
        <DialogTitle>{t("sources.removeTitle", { title: removeTarget.title })}</DialogTitle>
        <DialogDescription>{t("sources.removeBody")}</DialogDescription>
      </DialogHeader>

      <div class="flex items-center gap-2">
        <Checkbox
          id="remove-history"
          bind:checked={removeHistory}
          aria-label={t("sources.removeHistory")}
        />
        <Label for="remove-history" class="text-small">{t("sources.removeHistory")}</Label>
      </div>

      {#if removeError}
        <Note tone="fail">{removeError}</Note>
      {/if}

      <DialogFooter>
        <Button variant="outline" onclick={closeRemove}>{t("common.cancel")}</Button>
        <Button variant="destructive" disabled={removing} onclick={() => void confirmRemove()}>
          {t("sources.removeConfirm")}
        </Button>
      </DialogFooter>
    {/if}
  </DialogContent>
</Dialog>
