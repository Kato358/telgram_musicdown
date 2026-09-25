<script lang="ts">
  /** 小屏导航抽屉（设计规范 §6.3）：<768px 侧栏收起后，导航在这里。
   *
   * 条目规格与 Sidebar 一致（42px 高、12px 圆角、激活项浅绿块）；
   * Esc 关闭并把焦点还给汉堡键（§7）。
   */
  import type { Component } from "svelte";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import MenuIcon from "@lucide/svelte/icons/menu";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import ScrollTextIcon from "@lucide/svelte/icons/scroll-text";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SettingsIcon from "@lucide/svelte/icons/settings";
  import XIcon from "@lucide/svelte/icons/x";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, NAV_ROUTES, pathOf, router, type RouteKey } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import Badge from "./Badge.svelte";

  const ICONS: Record<RouteKey, Component> = {
    dashboard: LayoutDashboardIcon,
    search: SearchIcon,
    downloads: DownloadIcon,
    sources: RadioTowerIcon,
    settings: SettingsIcon,
    logs: ScrollTextIcon,
    setup: SettingsIcon,
  };

  let open = $state(false);
  let trigger = $state<HTMLButtonElement | undefined>();

  function countFor(key: RouteKey): number {
    if (key === "downloads") return queue.activeCount;
    if (key === "logs") return events.errors.length;
    return 0;
  }

  function go(path: string) {
    close();
    navigate(path);
  }

  function close() {
    open = false;
    trigger?.focus();
  }
</script>

<svelte:window
  onkeydown={(event) => {
    if (open && event.key === "Escape") close();
  }}
/>

<button
  bind:this={trigger}
  type="button"
  data-fly-navmenu
  class="ui-transition grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground md:hidden"
  aria-label={t("app.menu")}
  aria-expanded={open}
  onclick={() => (open = true)}
>
  <MenuIcon class="size-5" aria-hidden="true" />
</button>

{#if open}
  <div class="fixed inset-0 z-40 bg-foreground/20 md:hidden" aria-hidden="true" onclick={close}></div>
  <nav
    class="fixed top-0 left-0 z-50 flex h-full w-[264px] flex-col border-r border-border bg-card p-3 md:hidden"
    aria-label={t("app.navLabel")}
  >
    <div class="mb-2 flex h-10 items-center justify-between gap-2 px-2">
      <span class="truncate text-body font-semibold">{t("app.name")}</span>
      <button
        type="button"
        class="ui-transition grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-rule hover:text-foreground"
        aria-label={t("common.cancel")}
        onclick={close}
      >
        <XIcon class="size-4" aria-hidden="true" />
      </button>
    </div>

    <div class="flex flex-col gap-0.5">
      {#each NAV_ROUTES as route (route.key)}
        {@const active = router.key === route.key}
        {@const Icon = ICONS[route.key]}
        <button
          type="button"
          class="ui-transition flex h-[42px] items-center gap-2.5 rounded-nav px-3 text-left {active
            ? 'bg-primary-soft text-primary'
            : 'text-muted-foreground hover:bg-rule hover:text-foreground'}"
          aria-current={active ? "page" : undefined}
          onclick={() => go(pathOf(route.key))}
        >
          <Icon class="size-[18px] shrink-0" aria-hidden="true" />
          <span class="min-w-0 flex-1 truncate text-body font-medium">
            {t(`nav.${route.key}`)}
          </span>
          <Badge count={countFor(route.key)} />
        </button>
      {/each}
    </div>
  </nav>
{/if}
