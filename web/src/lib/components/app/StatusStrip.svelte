<script lang="ts">
  /** 顶栏：会话真值（连接、账号）+ 全局读数（在传、速率）+ 外观开关。
   *  它是全站唯一的"仪表行"，不随路由变化（设计规范 §4）。 */
  import CheckIcon from "@lucide/svelte/icons/check";
  import SunMoonIcon from "@lucide/svelte/icons/sun-moon";
  import { Button } from "$lib/components/ui/button";
  import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
  } from "$lib/components/ui/dropdown-menu";
  import { t } from "$lib/i18n/index.svelte";
  import { formatRate } from "$lib/format";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { theme, THEME_OPTIONS } from "$lib/stores/theme.svelte";
  import Lamp from "./Lamp.svelte";
  import MobileNav from "./MobileNav.svelte";

  /** 总速率 = 当前下载中任务的进度帧速率之和（SSE 单点订阅，不自开连接）。 */
  const rate = $derived(
    queue.downloading.reduce((sum, task) => sum + (events.progress[task.id]?.speed ?? 0), 0),
  );
</script>

<header class="flex h-11 shrink-0 items-center gap-3 border-b border-rule bg-background px-3 lg:px-5">
  <MobileNav />
  <span class="hidden max-w-[9rem] truncate text-small font-semibold min-[420px]:inline lg:hidden">
    {t("app.name")}
  </span>

  <Lamp
    tone={session.connected ? "done" : "fail"}
    label={session.connected ? t("app.connected") : t("app.disconnected")}
  />
  {#if session.handle}
    <span class="tabular hidden text-micro text-muted-foreground sm:inline">{session.handle}</span>
  {/if}

  <div class="ml-auto flex items-center gap-3">
    <span class="tabular hidden text-micro text-muted-foreground sm:inline">
      {t("app.activeTransfers")} {queue.activeCount}
    </span>
    <span class="tabular hidden text-micro text-muted-foreground md:inline">
      {t("app.throughput")} {formatRate(rate)}
    </span>

    <DropdownMenu>
      <DropdownMenuTrigger>
        {#snippet child({ props })}
          <Button {...props} variant="ghost" size="icon-sm" aria-label={t("app.theme")}>
            <SunMoonIcon />
          </Button>
        {/snippet}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" class="min-w-36">
        {#each THEME_OPTIONS as option (option.value)}
          <DropdownMenuItem onSelect={() => theme.set(option.value)} class="justify-between">
            {t(option.label)}
            {#if theme.preference === option.value}
              <CheckIcon class="size-4" />
            {/if}
          </DropdownMenuItem>
        {/each}
      </DropdownMenuContent>
    </DropdownMenu>
  </div>
</header>
