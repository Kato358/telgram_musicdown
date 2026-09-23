/** 路由：history API + runes，量级只有 8 条静态路由，不引第三方路由库。
 *
 * 后端对所有非 /api 路径回退 index.html（SDD §4.3），故 history 模式可用。
 */

export type RouteKey =
  | "dashboard"
  | "search"
  | "tasks"
  | "history"
  | "sources"
  | "settings"
  | "logs"
  | "setup";

export const ROUTES: { key: RouteKey; path: string }[] = [
  { key: "dashboard", path: "/dashboard" },
  { key: "search", path: "/search" },
  { key: "tasks", path: "/tasks" },
  { key: "history", path: "/history" },
  { key: "sources", path: "/sources" },
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

class Router {
  /** 当前规范化路径，如 "/dashboard"。 */
  path = $state<string>(normalize(window.location.pathname));

  get key(): RouteKey {
    return keyOf(this.path) ?? DEFAULT_ROUTE;
  }

  get known(): boolean {
    return keyOf(this.path) !== null;
  }

  constructor() {
    window.addEventListener("popstate", () => {
      this.path = normalize(window.location.pathname);
    });
  }
}

function normalize(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, "");
  if (trimmed === "" || trimmed === "/") return "/dashboard";
  return trimmed;
}

export const router = new Router();

export function navigate(to: string, options?: { replace?: boolean }): void {
  const target = normalize(to);
  if (options?.replace) {
    window.history.replaceState(null, "", target);
  } else if (target !== router.path) {
    window.history.pushState(null, "", target);
  }
  router.path = target;
  window.scrollTo({ top: 0 });
}
