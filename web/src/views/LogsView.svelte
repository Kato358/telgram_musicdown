<script lang="ts">
  /** 日志：只保留本次连接期间 SSE 推来的错误事件（编码规范 §5：唯一订阅点是 events store）。
   *
   * 这些是机器串与路径，所以消息一律等宽；时间戳只给到秒，够定位即可。
   */
  import { t } from "$lib/i18n/index.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { Button } from "$lib/components/ui/button";
  import EmptyState from "$lib/components/app/EmptyState.svelte";
  import PageHeader from "$lib/components/app/PageHeader.svelte";

  function timeOf(ts: number): string {
    return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
  }
</script>

<div class="flex flex-col gap-5">
  <PageHeader title={t("logs.title")} lede={t("logs.lede")}>
    {#snippet aside()}
      <span class="tabular text-micro text-muted-foreground">
        {t("logs.count", { n: events.errors.length })}
      </span>
      <Button
        variant="ghost"
        size="xs"
        disabled={events.errors.length === 0}
        onclick={() => events.clearErrors()}
      >
        {t("logs.clear")}
      </Button>
    {/snippet}
  </PageHeader>

  {#if events.errors.length === 0}
    <EmptyState title={t("logs.empty")} />
  {:else}
    <ul class="divide-y divide-rule">
      {#each events.errors as entry (entry.id)}
        <li class="flex gap-3 py-2">
          <span class="tabular shrink-0 text-micro text-muted-foreground">{timeOf(entry.ts)}</span>
          <span class="tabular min-w-0 flex-1 text-small text-lamp-fail break-all">
            {entry.message}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</div>
