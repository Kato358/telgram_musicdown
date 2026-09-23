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

  async signOut() {
    await api.post("/api/auth/logout");
    this.me = { display_name: null, username: null, premium: false, connected: false };
  }
}

export const session = new Session();
