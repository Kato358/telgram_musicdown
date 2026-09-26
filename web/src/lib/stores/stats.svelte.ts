/** 控制台统计快照（`GET /api/stats`）：统计卡的唯一数据源。
 *
 * 与 `queue` 同一套路——事实源在后端，SSE 只当失效信号：页面在挂载时取一次，
 * 之后由 `events.revision`（任务状态变化）驱动重取；这里不起第二个定时器，
 * 队列本身的 10s 轮询已经把「任务变了」这件事带出来了。
 */

import { api } from "$lib/api/client";
import type { StatsResponse } from "$lib/api/types";

class Stats {
  data = $state<StatsResponse | null>(null);

  async refresh() {
    try {
      this.data = await api.get<StatsResponse>("/api/stats");
    } catch {
      // 快照取不到就保持上一份：各页面自己有 error 展示，这里不再重复存一份错误
    }
  }
}

export const stats = new Stats();
