/** 视图登记处：地址栏 → 按需取分块，首屏只装「真的要立刻渲染的那一块」。
 *
 * 八个视图原先全是 App.svelte 的静态 import，和 aplayer、bits-ui 一起落在同一块
 * 616 kB 的 entry chunk 里：每次冷启动都得先把设置页、日志页这些多半不会立刻打开的
 * 字节下完，才轮得到首屏。改成按需加载后，entry 只留外壳、闸门、仪表盘。
 *
 * 「按需」不等于「点一下等半天」，靠两件事兜住：
 * - 加载完的组件留在 #loaded：再次进入是同步渲染，不会重放一次骨架；
 * - 控制台可见后空闲预取其余分块（prefetchAll），切页时多半已经躺在缓存里。
 */

import type { Component } from "svelte";
import type { RouteKey } from "$lib/router.svelte";
import DashboardView from "@/views/DashboardView.svelte";

/** 按需分块的视图。仪表盘不在此列：它静态引入并预置进 #loaded（见下），
 *  再挂一个 import() 只会让打包器把模块拆错地方。 */
const LOADERS: Partial<Record<RouteKey, () => Promise<{ default: Component }>>> = {
  search: () => import("@/views/SearchView.svelte"),
  downloads: () => import("@/views/DownloadsView.svelte"),
  sources: () => import("@/views/SourcesView.svelte"),
  library: () => import("@/views/LocalLibraryView.svelte"),
  settings: () => import("@/views/SettingsView.svelte"),
  logs: () => import("@/views/LogsView.svelte"),
  setup: () => import("@/views/SetupView.svelte"),
};

class Views {
  /** 已就位的组件；模板读它决定渲染视图还是骨架。
   *  仪表盘开机就在：它几乎总是登录/初始化闸门之后的首屏，不值得为它多等一次往返；
   *  其余视图都不必为「可能不会打开」先垫字节。 */
  #loaded = $state<Partial<Record<RouteKey, Component>>>({ dashboard: DashboardView });
  /** 在飞的 import。刻意不放进 $state：ensure 只据此去重，不参与渲染依赖。 */
  #pending = new Map<RouteKey, Promise<unknown>>();
  /** 空闲预取只做一次（调用它的效果会随路由反复跑）。 */
  #prefetched = false;

  /** 已加载好的视图；还没到返回 null，调用方据此显示骨架。 */
  get(key: RouteKey): Component | null {
    return this.#loaded[key] ?? null;
  }

  /** 保证 key 的分块正在加载；幂等，重复调用不会重复请求。 */
  ensure(key: RouteKey): void {
    const load = LOADERS[key];
    if (!load || this.#loaded[key] || this.#pending.has(key)) return;
    const inflight = load().then((module) => {
      this.#pending.delete(key);
      this.#loaded[key] = module.default;
    });
    this.#pending.set(key, inflight);
  }

  /** 控制台已经出来、浏览器空下来之后把其余分块取回本地：
   *  冷启动的字节一个不多，切页却不必再等网络。 */
  prefetchAll(): void {
    if (this.#prefetched) return;
    this.#prefetched = true;
    // Safari 至今没有 requestIdleCallback，退到定时器，同样不与首屏抢主线程
    const idle = (task: () => void): void => {
      if (typeof window.requestIdleCallback === "function") {
        window.requestIdleCallback(task);
      } else {
        window.setTimeout(task, 200);
      }
    };
    for (const key of Object.keys(LOADERS) as RouteKey[]) {
      idle(() => this.ensure(key));
    }
  }
}

export const views = new Views();
