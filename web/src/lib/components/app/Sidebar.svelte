<script lang="ts">
  /** 侧边导航（设计规范 §5.1）：≥1024px 固定 260px 展开（侧栏:主栏 ≈ 1:4~1:5），
   *  <1024px 整条收进顶栏汉堡抽屉（MobileNav），没有中间的图标模式。
   *
   * 当前页用浅绿圆角块包裹，不用左条标记；计数用浅绿药丸，为 0 时不渲染。
   */
  import type { Component } from "svelte";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import LibraryIcon from "@lucide/svelte/icons/library";
  import MusicIcon from "@lucide/svelte/icons/music";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import ScrollTextIcon from "@lucide/svelte/icons/scroll-text";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SettingsIcon from "@lucide/svelte/icons/settings";
  import { t } from "$lib/i18n/index.svelte";
  import { formatCount, formatSize } from "$lib/format";
  import { NAV_ROUTES, pathOf, router, type RouteKey } from "$lib/router.svelte";
import { events } from "$lib/stores/events.svelte";
import { fly } from "$lib/stores/fly.svelte";
import { queue } from "$lib/stores/queue.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import Badge from "./Badge.svelte";
  import Link from "./Link.svelte";

  const ICONS: Record<RouteKey, Component> = {
    dashboard: LayoutDashboardIcon,
    search: SearchIcon,
    downloads: DownloadIcon,
    sources: RadioTowerIcon,
    library: LibraryIcon,
    settings: SettingsIcon,
    logs: ScrollTextIcon,
    setup: SettingsIcon,
  };

  /** 徽章：下载项 = 进行中数，日志项 = 连接期错误数。 */
  function countFor(key: RouteKey): number {
    if (key === "downloads") return queue.activeCount;
    if (key === "logs") return events.errors.length;
    return 0;
  }
</script>

<aside class="hidden shrink-0 flex-col border-r border-border bg-card lg:flex lg:w-[260px]">
  <div class="flex h-16 shrink-0 items-center gap-2.5 px-4">
    <span
      class="grid size-7 shrink-0 place-items-center rounded-chip bg-primary text-primary-foreground"
      aria-hidden="true"
    >
      <MusicIcon class="size-4" />
    </span>
    <span class="min-w-0 flex-col">
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
        class="ui-transition flex h-[42px] items-center gap-2.5 rounded-nav px-3 {active
          ? 'bg-primary-soft text-primary'
          : 'text-muted-foreground hover:bg-rule hover:text-foreground'}"
      >
        {#if route.key === "downloads"}
          <!-- 搜索页加入队列后飞片飞到 data-fly-downloads 这个点；fly.pulse 递增一次
              就靠 {#key} 重挂重放一次落定弹跳，初始（pulse=0）不弹。 -->
          {#key fly.pulse}
            <span
              data-fly-downloads
              class="grid size-[18px] shrink-0 place-items-center {fly.pulse > 0 ? 'fly-pop' : ''}"
            >
              <Icon class="size-[18px]" />
            </span>
          {/key}
        {:else}
          <Icon class="size-[18px] shrink-0" />
        {/if}
        <span class="min-w-0 flex-1 truncate text-body font-medium">
          {t(`nav.${route.key}`)}
        </span>
        <Badge count={countFor(route.key)} />
      </Link>
    {/each}
  </nav>

  <!-- 底部的本地曲库入口（左下角）：整块进曲库页（/library），扫描 downloads 落盘文件的
      独立台账——文件删了记录仍在。侧栏只在 ≥1024px 渲染，抽屉模式入口在 MobileNav 底部。 -->
  <div class="px-3 pb-4">
    <Link
      href={pathOf("library")}
      active={router.key === "library"}
      class="surface-promo ui-transition block rounded-nav p-3 hover:opacity-90"
      title={t("sidebar.library")}
    >
      <span
        class="grid size-8 shrink-0 place-items-center rounded-chip bg-card/70 text-primary"
        aria-hidden="true"
      >
        <LibraryIcon class="size-4" />
      </span>
      <p class="mt-2 truncate text-body font-semibold">{t("sidebar.library")}</p>
      <p class="tabular truncate text-caption text-muted-foreground">
        {t("sidebar.libraryFacts", {
          n: formatCount(stats.data?.library.local_present ?? null),
          size: formatSize(stats.data?.library.local_bytes ?? null),
        })}
      </p>
      <p class="truncate text-caption text-muted-foreground">{t("sidebar.libraryHint")}</p>
    </Link>
  </div>
</aside>
