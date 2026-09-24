/** 队列快照：任务列表的唯一缓存。
 *
 * 事实源是 DB（`GET /api/downloads`），SSE 只负责推快照失效信号：
 * 收到 task.status 立即重取，另外每 10s 兜底轮询（编码规范 §5）。
 */

import { api } from "$lib/api/client";
import type { TaskRow } from "$lib/api/types";
import { events } from "$lib/stores/events.svelte";

const POLL_MS = 10_000;

class Queue {
  tasks = $state<TaskRow[]>([]);
  loaded = $state(false);

  #timer: number | undefined = undefined;
  #unsubscribe: (() => void) | null = null;

  get queued(): TaskRow[] {
    return this.tasks.filter((t) => t.status === "queued");
  }

  get downloading(): TaskRow[] {
    return this.tasks.filter((t) => t.status === "downloading");
  }

  get paused(): TaskRow[] {
    return this.tasks.filter((t) => t.status === "paused");
  }

  /** 进行中 = 等待 + 下载中 + 已暂停；顶栏计数与导航角标用。 */
  get activeCount(): number {
    return this.tasks.filter((t) => ["queued", "downloading", "paused"].includes(t.status)).length;
  }

  get failedCount(): number {
    return this.tasks.filter((t) => t.status === "failed").length;
  }

  async refresh() {
    this.tasks = await api.get<TaskRow[]>("/api/downloads");
    this.loaded = true;
  }

  start() {
    if (this.#timer !== undefined) return;
    void this.refresh();
    this.#unsubscribe = events.onStatus((event) => {
      if (event.status !== "downloading") {
        events.forget(event.task_id);
      }
      void this.refresh();
    });
    this.#timer = window.setInterval(() => void this.refresh(), POLL_MS);
  }

  stop() {
    clearInterval(this.#timer);
    this.#timer = undefined;
    this.#unsubscribe?.();
    this.#unsubscribe = null;
  }
}

export const queue = new Queue();
