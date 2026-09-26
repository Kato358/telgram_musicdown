/** 路由：history API + runes，量级只有 7 条静态路由，不引第三方路由库。
 *
 * 路径与查询串分开存：`path` 决定渲染哪个视图，`query` 承载跨页参数（顶栏搜索 → `/search?q=…`，
 * 仪表盘的「最近入库」→ `/downloads?status=success`）。
 * 后端对所有非 /api 路径回退 index.html（SDD §4.3），故 history 模式可用。
 */

export type RouteKey =
  | "dashboard"
  | "search"
  | "downloads"
  | "sources"
  | "library"
  | "settings"
  | "logs"
  | "setup";

export const ROUTES: { key: RouteKey; path: string }[] = [
  { key: "dashboard", path: "/dashboard" },
  { key: "search", path: "/search" },
  { key: "downloads", path: "/downloads" },
  { key: "sources", path: "/sources" },
  { key: "library", path: "/library" },
  { key: "settings", path: "/settings" },
  { key: "logs", path: "/logs" },
  { key: "setup", path: "/setup" },
];

/** 侧栏顺序（setup 不在主导航里，由初始化闸门进入）。 */
export const NAV_ROUTES = ROUTES.filter((r) => r.key !== "setup");

export const DEFAULT_ROUTE: RouteKey = "dashboard";

function keyOf(pathname: string): RouteKey | null {
  const found = ROUTES.find((r) => r.path === pathname.replace(/\/+$/, "") || r.path === pathname);
  return found ? found.key : null;
}

export function pathOf(key: RouteKey): string {
  return ROUTES.find((r) => r.key === key)?.path ?? "/dashboard";
}

/** 只取路径部分（丢掉查询串），空路径回落到默认路由。 */
function normalize(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, "");
  if (trimmed === "" || trimmed === "/") return "/dashboard";
  return trimmed;
}

class Router {
  /** 当前规范化路径，如 "/search"。 */
  path = $state<string>(normalize(window.location.pathname));
  /** 当前查询串，如 "?q=晴天"；无参数时为空串。 */
  search = $state<string>(window.location.search);

  get key(): RouteKey {
    return keyOf(this.path) ?? DEFAULT_ROUTE;
  }

  get known(): boolean {
    return keyOf(this.path) !== null;
  }

  /** 当前 URL 的查询参数（视图读 `?q=` 这类跨页入参用）。 */
  get query(): URLSearchParams {
    return new URLSearchParams(this.search);
  }

  constructor() {
    window.addEventListener("popstate", () => {
      this.path = normalize(window.location.pathname);
      this.search = window.location.search;
    });
  }
}

export const router = new Router();

export function navigate(to: string, options?: { replace?: boolean }): void {
  const { path, search } = splitHref(to);
  const target = normalize(path);
  if (options?.replace) {
    window.history.replaceState(null, "", target + search);
  } else if (target !== router.path || search !== router.search) {
    window.history.pushState(null, "", target + search);
  }
  router.path = target;
  router.search = search;
  window.scrollTo({ top: 0 });
}

/** 拆出路径与查询串（`/search?q=x` → `{ path: "/search", search: "?q=x" }`）。 */
function splitHref(href: string): { path: string; search: string } {
  const cut = href.indexOf("?");
  if (cut < 0) return { path: href, search: "" };
  return { path: href.slice(0, cut), search: href.slice(cut) };
}
