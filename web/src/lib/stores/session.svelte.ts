/** 会话状态：Web 控制台登录 + 账号信息与初始化闸门（FR-WEB-02、FR-AUTH-01/02、FR-OPS-02）。 */

import { api } from "$lib/api/client";
import type { MeResponse, SetupStatus, WebSessionStatus } from "$lib/api/types";

class Session {
  me = $state<MeResponse | null>(null);
  setup = $state<SetupStatus | null>(null);
  /** 初始化状态已确认；未确认前不渲染控制台，避免闪进再跳走。 */
  checked = $state(false);
  /** Web 控制台准入：受保护部署需先登录（本机免密或登录开关关闭时 required=false）。 */
  web = $state<WebSessionStatus | null>(null);

  get connected(): boolean {
    return this.me?.connected ?? false;
  }

  get handle(): string | null {
    if (!this.me?.connected) return null;
    return this.me.display_name ?? this.me.username ?? null;
  }

  /** 是否需要且尚未通过 Web 登录（外壳据此渲染登录页）。 */
  get needsWebLogin(): boolean {
    return this.web?.required === true && !this.web.authenticated;
  }

  /** 搜索模式（FR-SEARCH-01）：`global` = 全账号搜索（不需要音乐源）。
   *
   *  状态还没取到时按保守的 `sources` 处理：多显示一个音乐源入口，好过把源配置藏起来
   *  让人找不到（后端缺省也是 `sources`）。
   */
  get searchMode(): string {
    return this.setup?.search_mode ?? "sources";
  }

  /** 全账号搜索：导航/仪表盘/搜索页据此收起音乐源相关入口。 */
  get globalSearch(): boolean {
    return this.searchMode === "global";
  }

  /** 切换搜索模式（设置页与向导共用）：保存后重取闸门状态，导航随即跟着变。 */
  async setSearchMode(mode: string) {
    await api.put<Record<string, string>>("/api/settings", { values: { search_mode: mode } });
    await this.loadSetup();
  }

  async loadWebSession() {
    this.web = await api.get<WebSessionStatus>("/api/auth/session");
  }

  /** 用 web_login_secret 换会话 cookie（FR-WEB-02）。
   *
   * 登录成功后先补齐外壳 onMount 跳过的引导（loadSetup/loadMe），最后才放行登录闸门：
   * 受保护部署未登录时外壳提前 return，若不补，setup 仍是 null，setupGate 会把
   * 已完成初始化的人钉回向导。引导失败不报错——与刷新时一致，交给初始化闸门
   * 「停在向导并写原因」的路径。 */
  async webLogin(secret: string) {
    await api.post("/api/auth/login", { secret });
    await this.loadSetup().catch(() => undefined);
    try {
      await this.loadMe();
    } catch {
      // 账号信息取不到不影响进门
    }
    await this.loadWebSession();
  }

  /** 退出 Web 控制台（只清 cookie，不动 Telegram 会话）。 */
  async webLogout() {
    await api.post("/api/auth/session/logout");
    this.web = { required: true, authenticated: false };
    this.me = null;
    this.setup = null;
  }

  async loadMe() {
    this.me = await api.get<MeResponse>("/api/me");
  }

  async loadSetup() {
    try {
      this.setup = await api.get<SetupStatus>("/api/setup/status");
    } finally {
      // 失败也算「已确认」：闸门据此停在向导（页头写原因），否则首屏会永远停在加载态
      this.checked = true;
    }
  }

  /** 退出 Telegram 账号（FR-AUTH-02）：服务端删会话文件，随即回到初始化向导。 */
  async signOut() {
    await api.post("/api/auth/logout");
    this.me = { display_name: null, username: null, premium: false, connected: false };
    await this.loadSetup(); // 放行判据含登录：退出后 complete 变 false，闸门自己会把人送回向导
  }
}

export const session = new Session();
