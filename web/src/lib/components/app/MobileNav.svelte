<script lang="ts">
  /** 小屏导航抽屉：<1024px 收起侧栏后，导航在这里（FR-WEB-06）。 */
  import type { Component } from "svelte";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import HistoryIcon from "@lucide/svelte/icons/history";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import MenuIcon from "@lucide/svelte/icons/menu";
  import RadioTowerIcon from "@lucide/svelte/icons/radio-tower";
  import ScrollTextIcon from "@lucide/svelte/icons/scroll-text";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SettingsIcon from "@lucide/svelte/icons/settings";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, NAV_ROUTES, pathOf, type RouteKey } from "$lib/router.svelte";
  import { Button } from "$lib/components/ui/button";
  import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
  } from "$lib/components/ui/dialog";

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

  let open = $state(false);

  function go(path: string) {
    open = false;
    navigate(path);
  }
</script>

<Dialog bind:open>
  <DialogTrigger>
    {#snippet child({ props })}
      <Button {...props} variant="ghost" size="icon-sm" class="lg:hidden" aria-label={t("app.menu")}>
        <MenuIcon />
      </Button>
    {/snippet}
  </DialogTrigger>
  <DialogContent class="max-w-xs">
    <DialogHeader>
      <DialogTitle>{t("app.name")}</DialogTitle>
    </DialogHeader>
    <nav class="flex flex-col" aria-label={t("app.navLabel")}>
      {#each NAV_ROUTES as route (route.key)}
        {@const Icon = ICONS[route.key]}
        <button
          type="button"
          class="flex h-10 items-center gap-2.5 border-b border-rule text-left text-small text-muted-foreground last:border-b-0 hover:text-foreground"
          onclick={() => go(pathOf(route.key))}
        >
          <Icon class="size-4 shrink-0" />
          {t(`nav.${route.key}`)}
        </button>
      {/each}
    </nav>
  </DialogContent>
</Dialog>
