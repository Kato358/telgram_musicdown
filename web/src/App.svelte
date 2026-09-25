<script lang="ts">
  /** 应用外壳：初始化闸门 + 侧栏/顶栏/内容区三段结构 + 吸底播放器（设计规范 §2.1、SDD §5.1）。
   *
   * 全站唯一常驻的：导航、顶栏、内容出口、APlayer 吸底播放器（PlayerHost）。
   * 其余都是路由内容。键盘快捷键只在注册一次——顶栏搜索的 Ctrl/⌘+K 从这里发信号，
   * 不在组件里各挂各的。
   */
  import { onMount } from "svelte";
  import type { Component } from "svelte";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf, router, type RouteKey } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { theme } from "$lib/stores/theme.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import Sidebar from "$lib/components/app/Sidebar.svelte";
  import TopBar from "$lib/components/app/TopBar.svelte";
  import PlayerHost from "$lib/components/app/PlayerHost.svelte";
  import FlyOverlay from "$lib/components/app/FlyOverlay.svelte";
  import DashboardView from "@/views/DashboardView.svelte";
  import DownloadsView from "@/views/DownloadsView.svelte";
  import LogsView from "@/views/LogsView.svelte";
  import SearchView from "@/views/SearchView.svelte";
  import SettingsView from "@/views/SettingsView.svelte";
  import SetupView from "@/views/SetupView.svelte";
  import SourcesView from "@/views/SourcesView.svelte";

  const VIEWS: Record<RouteKey, Component> = {
    dashboard: DashboardView,
    search: SearchView,
    downloads: DownloadsView,
    sources: SourcesView,
    settings: SettingsView,
    logs: LogsView,
    setup: SetupView,
  };

  /** Ctrl/⌘+K 的单一注册点：递增信号，由 TopBar 聚焦搜索框。 */
  let searchFocus = $state(0);

  $effect(() => {
    const label = router.key === "setup" ? t("setup.title") : t(`nav.${router.key}`);
    document.title = `${label} · ${t("app.name")}`;
  });

  /** 未确认「初始化完成」就不渲染控制台：状态取不到（401 / 网络不通）也按未初始化处理——
   *  宁可停在向导（它自带原因提示与修复入口），也不要卡在加载态或放一个用不了的控制台进去。
   *  注意「已确认」这一半不能省：`checked` 之前 setup 还是 null，照它跳转会把已完成初始化的
   *  用户也钉在向导里。 */
  const setupGate = $derived(session.checked && !session.setup?.complete);

  /** 地址栏与渲染保持一致：强制进向导时用 replace，不留一条控制台历史。 */
  $effect(() => {
    if (setupGate && router.key !== "setup") {
      navigate(pathOf("setup"), { replace: true });
    }
  });

  /** 统计快照的生命周期归外壳管：开屏取一次，之后任务状态一变就重取。
   *  这样侧栏与各页读的是同一份数字，页面不再各自发一次 `/api/stats`。 */
  $effect(() => {
    if (events.revision === 0) return; // 首次挂载由 onMount 负责，避免开屏打两次
    void stats.refresh();
  });

  /** 主题落到 <html>：浅/深唯一出口（首帧由 index.html 内联脚本先铺一次）。 */
  $effect(() => {
    const dark = theme.resolved === "dark";
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  });

  onMount(() => {
    theme.start();
    if (window.location.pathname !== router.path) {
      navigate(router.path + router.search, { replace: true });
    }
    events.connect();
    queue.start();
    void stats.refresh();
    void (async () => {
      try {
        await session.loadSetup(); // 失败也会置 checked：闸门据此停在向导并写明原因
        await session.loadMe();
      } catch {
        // 账号信息取不到不影响闸门判断：未完成初始化就留在向导
      }
    })();
    const onKeydown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchFocus += 1;
      }
    };
    window.addEventListener("keydown", onKeydown);
    return () => {
      window.removeEventListener("keydown", onKeydown);
      events.disconnect();
      queue.stop();
    };
  });
</script>

{#if !session.checked}
  <div class="grid min-h-dvh place-items-center text-caption text-muted-foreground">
    {t("common.loading")}
  </div>
{:else if setupGate || router.key === "setup"}
  <div class="min-h-dvh bg-background">
    <SetupView />
  </div>
{:else}
  <div class="flex h-dvh flex-col overflow-hidden bg-background">
    <div class="flex min-h-0 flex-1">
      <Sidebar />
      <div class="flex min-w-0 flex-1 flex-col">
        <TopBar focusSignal={searchFocus} />
        <main class="min-h-0 flex-1 overflow-y-auto">
          <div class="mx-auto w-full max-w-[1100px] px-4 py-6 md:px-6">
            {#key router.key}
              {@const View = VIEWS[router.key]}
              <div class="route-fade flex flex-col gap-4 md:gap-6">
                <View />
              </div>
            {/key}
          </div>
        </main>
      </div>
    </div>
    <PlayerHost />
    <!-- 「飞进侧边栏下载」的全局动画层：fixed 定位，挂在外壳上与路由无关 -->
    <FlyOverlay />
  </div>
{/if}
