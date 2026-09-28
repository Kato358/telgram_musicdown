<script lang="ts">
  /** 应用外壳：初始化闸门 + 侧栏/顶栏/内容区三段结构 + 吸底播放器（设计规范 §2.1、SDD §5.1）。
   *
   * 全站唯一常驻的：导航、顶栏、内容出口、APlayer 吸底播放器（PlayerHost）。
   * 其余都是路由内容。键盘快捷键只在注册一次——顶栏搜索的 Ctrl/⌘+K 从这里发信号，
   * 不在组件里各挂各的。
   *
   * 视图也不在这里静态 import：路由内容按地址栏取分块（$lib/views.svelte），
   * 冷启动只装外壳与闸门这两件必须先有的事。
   */
  import { onMount } from "svelte";
  import { t } from "$lib/i18n/index.svelte";
  import { navigate, pathOf, router } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { theme } from "$lib/stores/theme.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { stats } from "$lib/stores/stats.svelte";
  import Sidebar from "$lib/components/app/Sidebar.svelte";
  import TopBar from "$lib/components/app/TopBar.svelte";
  import PlayerHost from "$lib/components/app/PlayerHost.svelte";
  import ImmersivePlayer from "$lib/components/app/ImmersivePlayer.svelte";
  import FlyOverlay from "$lib/components/app/FlyOverlay.svelte";
  import LoginView from "@/views/LoginView.svelte";
  import { views } from "$lib/views.svelte";

  /** Ctrl/⌘+K 的单一注册点：递增信号，由 TopBar 聚焦搜索框。 */
  let searchFocus = $state(0);

  /** 明暗切换过渡的时长（与 app.css `.theme-transition` 的 240ms 同一份预算：路由进场同档）。 */
  const THEME_FADE_MS = 240;

  $effect(() => {
    const label = router.key === "setup" ? t("setup.title") : t(`nav.${router.key}`);
    document.title = `${label} · ${t("app.name")}`;
  });

  /** 未确认「初始化完成」就不渲染控制台：状态取不到（401 / 网络不通）也按未初始化处理——
   *  宁可停在向导（它自带原因提示与修复入口），也不要卡在加载态或放一个用不了的控制台进去。
   *  注意「已确认」这一半不能省：`checked` 之前 setup 还是 null，照它跳转会把已完成初始化的
   *  用户也钉在向导里。 */
  const setupGate = $derived(session.checked && !session.setup?.complete);

  /** Web 准入闸门（FR-WEB-02）：受保护部署未登录时先渲染登录页。
   *  必须排在初始化闸门之前——没有会话 cookie 时 setup 状态本身也是 401，
   *  先问登录再问初始化，否则会把人误导向向导。 */
  const loginGate = $derived(session.web?.required === true && !session.web.authenticated);

  /** 地址栏与渲染保持一致：强制进向导时用 replace，不留一条控制台历史。
   *
   *  登录闸门期间绝不能跳：那时 `checked` 已为真（登录页自身就是首屏）而 setup 还是
   *  null，照 setupGate 判断必然成立，会在用户还在输口令时就把地址栏写成 /setup；
   *  登录一过 setupGate 转假、渲染回控制台，但地址栏已是 /setup，于是渲染出向导再由
   *  SetupView 弹回仪表盘——「登录后闪一下向导」。初始化状态没拿到之前不跳，与渲染
   *  顺序同一理由：先问登录，再问初始化。 */
  $effect(() => {
    if (setupGate && !loginGate && router.key !== "setup") {
      navigate(pathOf("setup"), { replace: true });
    }
  });

  /** 当前该渲染哪个视图：闸门优先于地址栏，与下方渲染分支同一套判断。
   *  初始化没完成时地址栏还写着 /dashboard，真正要渲染的是向导。 */
  const activeView = $derived(setupGate || router.key === "setup" ? "setup" : router.key);

  /** 分块加载跟着 activeView 走。登录闸门期间一个视图都不渲染，也就不必先取分块；
   *  初始化未定的那段窗口照常先取——分块下载与「会话/初始化」两次 API 并行跑，
   *  闸门一过直接出内容，而不是过了闸门才开始等网络。 */
  $effect(() => {
    if (loginGate) return;
    views.ensure(activeView);
  });

  /** 控制台一出来就把其余视图分块空闲预取回本地：切页不等人，冷启动的字节不增。 */
  $effect(() => {
    if (!session.checked || loginGate || setupGate || router.key === "setup") return;
    views.prefetchAll();
  });

  /** 统计快照的生命周期归外壳管：开屏取一次，之后任务状态一变就重取。
   *  这样侧栏与各页读的是同一份数字，页面不再各自发一次 `/api/stats`。 */
  $effect(() => {
    if (events.revision === 0) return; // 首次挂载由 onMount 负责，避免开屏打两次
    void stats.refresh();
  });

  /** SSE 生命周期跟着 Web 准入走（FR-WEB-02）：`/api/events` 未登录恒 401，登录页
   *  连它只是白连一条死流（EventSource 对 401 不自动重试），过了登录闸门（含本机
   *  免密部署）才连，登出即断。connect() 幂等，登录引起的重复进入只会连一次。 */
  $effect(() => {
    if (!session.checked || session.needsWebLogin) {
      events.disconnect();
      return;
    }
    events.connect();
  });

  /** 主题落到 <html>：浅/深唯一出口（首帧由 index.html 内联脚本先铺一次）。
   *  换主题时给 <html> 挂一次 `.theme-transition`（app.css 的 240ms 颜色过渡），过后摘掉——
   *  常驻 transition 会与 hover 的 `.ui-transition`、路由进场的 animation 抢同一批属性。
   *  `appliedTheme === null` 那一趟是首帧：开屏没有「从旧主题过渡过来」这回事，不挂。 */
  let appliedTheme: "light" | "dark" | null = null;
  let fadeTimer: ReturnType<typeof setTimeout> | undefined;

  $effect(() => {
    const root = document.documentElement;
    const next = theme.resolved === "dark" ? "dark" : "light";
    if (appliedTheme !== null && appliedTheme !== next) {
      root.classList.add("theme-transition");
      clearTimeout(fadeTimer);
      fadeTimer = setTimeout(() => root.classList.remove("theme-transition"), THEME_FADE_MS);
    }
    appliedTheme = next;
    root.classList.toggle("dark", next === "dark");
    root.style.colorScheme = next;
  });

  onMount(() => {
    theme.start();
    if (window.location.pathname !== router.path) {
      navigate(router.path + router.search, { replace: true });
    }
    // SSE 不在这里连：受保护部署此时多半还没登录，抢先连只会收获一条 401 死流
    // （connect() 见 #source 非空会早退，之后谁也救不回来）——生命周期全权交给准入 effect
    queue.start();
    void stats.refresh();
    void (async () => {
      try {
        // 先问 Web 准入：受保护部署未登录时其余调用都是 401，先取 setup 只会白拿一个错误
        await session.loadWebSession();
        if (session.needsWebLogin) {
          session.checked = true; // 登录页自身就是首屏，不必再等初始化状态
          return;
        }
        await session.loadSetup(); // 失败也会置 checked：闸门据此停在向导并写明原因
        await session.loadMe();
      } catch {
        // 账号信息取不到不影响闸门判断：未完成初始化就留在向导
        session.checked = true;
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
  <!-- 首屏骨架（v3.21）：形状先就位、内容落进来时不跳（比一句「加载中」少让人猜）。
       结构与控制台一致：侧栏 / 顶栏搜索 / 横幅 + 四张统计卡 + 表卡。文字只留给读屏。 -->
  <div class="flex h-dvh flex-col overflow-hidden bg-background" aria-busy="true">
    <div class="flex min-h-0 flex-1">
      <div
        class="hidden shrink-0 flex-col gap-2 border-r border-border bg-card p-3 lg:flex lg:w-[260px]"
      >
        <div class="skeleton h-10 rounded-nav"></div>
        {#each Array.from({ length: 6 }) as _, i (i)}
          <div class="skeleton h-8 rounded-nav"></div>
        {/each}
      </div>
      <div class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div class="flex h-16 shrink-0 items-center border-b border-border px-4 md:px-6">
          <div class="skeleton h-9 w-full rounded-full md:max-w-[480px]"></div>
        </div>
        <div class="mx-auto w-full max-w-[1100px] flex-1 px-4 py-6 md:px-6">
          <div class="flex flex-col gap-4 md:gap-6">
            <div class="skeleton h-32 rounded-card"></div>
            <div class="grid grid-cols-2 gap-4 sm:grid-cols-4 sm:gap-5">
              {#each Array.from({ length: 4 }) as _, i (i)}
                <div class="skeleton h-28 rounded-card"></div>
              {/each}
            </div>
            <div class="skeleton h-64 rounded-card"></div>
          </div>
        </div>
      </div>
    </div>
    <span class="sr-only" role="status">{t("common.loading")}</span>
  </div>
{:else if loginGate}
  <LoginView />
{:else if setupGate || router.key === "setup"}
  {@const Setup = views.get("setup")}
  <div class="min-h-dvh">
    {#if Setup}
      <Setup />
    {:else}
      <!-- 向导是最大的一块视图分块：先给载入态，别让人对着一张空白页猜 -->
      <div class="flex min-h-dvh items-center justify-center" aria-busy="true">
        <span class="text-sm text-muted-foreground" role="status">{t("common.loading")}</span>
      </div>
    {/if}
  </div>
{:else}
  <div class="flex h-dvh flex-col overflow-hidden">
    <div class="flex min-h-0 flex-1">
      <Sidebar />
      <div class="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
        <TopBar focusSignal={searchFocus} />
        <main class="flex-1">
          <div class="mx-auto w-full max-w-[1100px] px-4 py-6 md:px-6">
            {#key router.key}
              {@const View = views.get(router.key)}
              <div class="route-fade flex flex-col gap-4 md:gap-6">
                {#if View}
                  <View />
                {:else}
                  <!-- 预取过的情况几乎走不到这里；只有冷启动直接落到某个未取过的路由才会 -->
                  <div aria-busy="true">
                    <div class="skeleton h-10 w-56 rounded-full"></div>
                    <div class="skeleton mt-4 h-64 rounded-card"></div>
                    <span class="sr-only" role="status">{t("common.loading")}</span>
                  </div>
                {/if}
              </div>
            {/key}
          </div>
        </main>
      </div>
    </div>
    <PlayerHost />
    <!-- 沉浸全屏：与播放器同一实例，fixed 定位铺满一屏，挂在外壳上与路由无关 -->
    <ImmersivePlayer />
    <!-- 「飞进侧边栏下载」的全局动画层：fixed 定位，挂在外壳上与路由无关 -->
    <FlyOverlay />
  </div>
{/if}
