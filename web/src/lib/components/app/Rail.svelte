<script lang="ts">
  /** 侧栏：身份 + 导航 + 计数。当前页由左侧 2px 琥珀条标记，不用填充药丸（设计规范 §4）。 */
  import type { Component } from "svelte";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HistoryIcon from "@lucide/svelte/icons/history";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import ScrollTextIcon from "@lucide/svelte/icons/scroll-text";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SettingsIcon from "@lucide/svelte/icons/settings";
  import { t } from "$lib/i18n/index.svelte";
  import { NAV_ROUTES, pathOf, router, type RouteKey } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import Link from "./Link.svelte";

  const ICONS: Record<RouteKey, Component> = {
    dashboard: LayoutDashboardIcon,
    search: SearchIcon,
    tasks: DownloadIcon,
    history: HistoryIcon,
    sources: RadioTowerIcon,
    settings: SettingsIcon,
    logs: ScrollTextIcon,
    setup: SettingsIcon,
  };

  function countFor(key: RouteKey): number {
    if (key === "tasks") return queue.activeCount;
    if (key === "logs") return events.errors.length;
    return 0;
  }
</script>

<aside class="hidden w-52 shrink-0 flex-col border-r border-rule bg-background lg:flex">
  <div class="border-b border-rule px-4 py-3.5">
    <p class="text-body font-semibold tracking-tight">{t("app.name")}</p>
    <p class="tabular mt-0.5 text-micro text-muted-foreground">{t("app.repo")}</p>
  </div>

  <nav class="flex flex-1 flex-col py-2" aria-label={t("app.navLabel")}>
    {#each NAV_ROUTES as route (route.key)}
      {@const active = router.key === route.key}
      {@const count = countFor(route.key)}
      {@const Icon = ICONS[route.key]}
      <Link
        href={pathOf(route.key)}
        {active}
        class="relative flex h-9 items-center gap-2.5 pr-3 pl-4 text-small transition-colors {active
          ? 'font-medium text-foreground'
          : 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
      >
        {#if active}
          <span class="absolute top-1.5 bottom-1.5 left-0 w-[2px] bg-primary" aria-hidden="true"
          ></span>
        {/if}
        <Icon class="size-4 shrink-0" />
        <span class="truncate">{t(`nav.${route.key}`)}</span>
        {#if count > 0}
          <span class="tabular ml-auto text-micro text-muted-foreground">{count}</span>
        {/if}
      </Link>
    {/each}
  </nav>
</aside>
