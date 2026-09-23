/** 会话状态：账号信息与初始化闸门（FR-AUTH-01/02、FR-OPS-02）。 */

import { api } from "$lib/api/client";
import type { MeResponse, SetupStatus } from "$lib/api/types";

class Session {
  me = $state<MeResponse | null>(null);
  setup = $state<SetupStatus | null>(null);
  /** 初始化状态已确认；未确认前不渲染控制台，避免闪进再跳走。 */
  checked = $state(false);

  get connected(): boolean {
    return this.me?.connected ?? false;
  }

  get handle(): string | null {
    if (!this.me?.connected) return null;
    return this.me.display_name ?? this.me.username ?? null;
  }

  async loadMe() {
    this.me = await api.get<MeResponse>("/api/me");
  }

  async loadSetup() {
    this.setup = await api.get<SetupStatus>("/api/setup/status");
    this.checked = true;
  }

  /** 退出 Telegram 账号（FR-AUTH-02）：服务端删会话文件，随即回到初始化向导。 */
  async signOut() {
    await api.post("/api/auth/logout");
    this.me = { display_name: null, username: null, premium: false, connected: false };
    await this.loadSetup(); // 放行判据含登录：退出后 complete 变 false，闸门自己会把人送回向导
  }
}

export const session = new Session();
