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

  async loadWebSession() {
    this.web = await api.get<WebSessionStatus>("/api/auth/session");
  }

  /** 用 web_login_secret 换会话 cookie（FR-WEB-02）。 */
  async webLogin(secret: string) {
    await api.post("/api/auth/login", { secret });
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
