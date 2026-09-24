<script lang="ts">
  /** 侧边导航（设计规范 §5.1）：190px 展开，768–1023px 塌为 64px 图标模式（<768 收入抽屉）。
   *
   * 当前页用浅绿圆角块包裹，不用左条标记；计数用浅绿药丸，为 0 时不渲染。
   */
  import type { Component } from "svelte";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HistoryIcon from "@lucide/svelte/icons/history";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import MusicIcon from "@lucide/svelte/icons/music";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import ScrollTextIcon from "@lucide/svelte/icons/scroll-text";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SettingsIcon from "@lucide/svelte/icons/settings";
  import { t } from "$lib/i18n/index.svelte";
  import { formatCount, formatSize } from "$lib/format";
  import { NAV_ROUTES, pathOf, router, type RouteKey } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import Badge from "./Badge.svelte";
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

  /** 徽章：任务项 = 进行中数，日志项 = 连接期错误数。 */
  function countFor(key: RouteKey): number {
    if (key === "tasks") return queue.activeCount;
    if (key === "logs") return events.errors.length;
    return 0;
  }
</script>

<aside class="hidden shrink-0 flex-col border-r border-border bg-card md:flex md:w-16 lg:w-[190px]">
  <div class="flex h-16 shrink-0 items-center gap-2.5 px-4">
    <span
      class="grid size-7 shrink-0 place-items-center rounded-chip bg-primary text-primary-foreground"
      aria-hidden="true"
    >
      <MusicIcon class="size-4" />
    </span>
    <span class="hidden min-w-0 flex-col lg:flex">
      <span class="truncate text-body font-semibold">{t("app.name")}</span>
      <span class="tabular truncate text-caption text-muted-foreground">{t("app.repo")}</span>
    </span>
  </div>

  <nav
    class="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-3 lg:px-3"
    aria-label={t("app.navLabel")}
  >
    {#each NAV_ROUTES as route (route.key)}
      {@const active = router.key === route.key}
      {@const Icon = ICONS[route.key]}
      <Link
        href={pathOf(route.key)}
        {active}
        class="ui-transition flex h-[42px] items-center justify-center gap-2.5 rounded-nav px-3 lg:justify-start {active
          ? 'bg-primary-soft text-primary'
          : 'text-muted-foreground hover:bg-rule hover:text-foreground'}"
        title={t(`nav.${route.key}`)}
      >
        <Icon class="size-[18px] shrink-0" />
        <span class="hidden min-w-0 flex-1 truncate text-body font-medium lg:block">
          {t(`nav.${route.key}`)}
        </span>
        <Badge count={countFor(route.key)} class="hidden lg:inline-flex" />
      </Link>
    {/each}
  </nav>

  <!-- 底部的曲库卡（参考图的侧栏底部位）：只回答「曲库现在多大」，整块点进历史页。
      64px 图标模式下不渲染（那里放不下两行字）。 -->
  <div class="hidden px-3 pb-4 lg:block">
    <Link
      href={pathOf("history")}
      class="surface-promo ui-transition block rounded-nav p-3 hover:opacity-90"
      title={t("sidebar.library")}
    >
      <span
        class="grid size-8 shrink-0 place-items-center rounded-chip bg-card/70 text-primary"
        aria-hidden="true"
      >
        <MusicIcon class="size-4" />
      </span>
      <p class="mt-2 truncate text-body font-semibold">{t("sidebar.library")}</p>
      <p class="tabular truncate text-caption text-muted-foreground">
        {t("sidebar.libraryFacts", {
          n: formatCount(stats.data?.library.tracks ?? null),
          size: formatSize(stats.data?.library.bytes ?? null),
        })}
      </p>
      <p class="truncate text-caption text-muted-foreground">{t("sidebar.libraryHint")}</p>
    </Link>
  </div>
</aside>
