<script lang="ts">
  /** 应用外壳：初始化闸门 + 侧栏/顶栏/内容区/播放条四段结构（SDD §5.1）。
   *
   * 全站唯一常驻的四处：导航、会话仪表行、内容出口、播放条。其余都是路由内容。
   */
  import { onMount } from "svelte";
  import type { Component } from "svelte";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf, router, type RouteKey } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { theme } from "$lib/stores/theme.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import Rail from "$lib/components/app/Rail.svelte";
  import StatusStrip from "$lib/components/app/StatusStrip.svelte";
  import TransportBar from "$lib/components/app/TransportBar.svelte";
  import DashboardView from "@/views/DashboardView.svelte";
  import HistoryView from "@/views/HistoryView.svelte";
  import LogsView from "@/views/LogsView.svelte";
  import SearchView from "@/views/SearchView.svelte";
  import SettingsView from "@/views/SettingsView.svelte";
  import SetupView from "@/views/SetupView.svelte";
  import SourcesView from "@/views/SourcesView.svelte";
  import TasksView from "@/views/TasksView.svelte";

  const VIEWS: Record<RouteKey, Component> = {
    dashboard: DashboardView,
    search: SearchView,
    tasks: TasksView,
    history: HistoryView,
    sources: SourcesView,
    settings: SettingsView,
    logs: LogsView,
    setup: SetupView,
  };

  $effect(() => {
    const label = router.key === "setup" ? t("setup.title") : t(`nav.${router.key}`);
    document.title = `${label} · ${t("app.name")}`;
  });

  /** 未完成初始化时强制进入向导；已完成则从向导放行。 */
  $effect(() => {
    if (!session.checked) return;
    if (!session.setup?.complete && router.key !== "setup") {
      navigate(pathOf("setup"), { replace: true });
    }
  });

  /** 主题落到 <html>：浅/深唯一出口（首帧由 index.html 内联脚本先铺一次）。 */
  $effect(() => {
    const dark = theme.resolved === "dark";
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  });

  onMount(() => {
    theme.start();
    if (window.location.pathname !== pathOf(router.key)) {
      navigate(pathOf(router.key), { replace: true });
    }
    events.connect();
    queue.start();
    void (async () => {
      try {
        await session.loadSetup();
        await session.loadMe();
      } catch {
        // 状态接口不可用时保持界面可用，由各页自行提示错误
      }
    })();
    return () => {
      events.disconnect();
      queue.stop();
    };
  });
</script>

{#if !session.checked}
  <div class="grid min-h-dvh place-items-center text-micro text-muted-foreground">
    {t("common.loading")}
  </div>
{:else if router.key === "setup"}
  <div class="min-h-dvh bg-background">
    <SetupView />
  </div>
{:else}
  <div class="flex h-dvh flex-col overflow-hidden bg-background">
    <div class="flex min-h-0 flex-1">
      <Rail />
      <div class="flex min-w-0 flex-1 flex-col">
        <StatusStrip />
        <main class="min-h-0 flex-1 overflow-y-auto">
          <div class="mx-auto w-full max-w-[1100px] px-4 py-6 lg:px-8">
            {#key router.key}
              {@const View = VIEWS[router.key]}
              <div class="route-fade">
                <View />
              </div>
            {/key}
          </div>
        </main>
      </div>
    </div>
    <TransportBar />
  </div>
{/if}
