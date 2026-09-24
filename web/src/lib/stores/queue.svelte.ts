/** 队列快照：任务列表的唯一缓存。
 *
 * 事实源是 DB（`GET /api/downloads`），SSE 只负责推快照失效信号：
 * 收到 task.status 立即重取，另外每 10s 兜底轮询（编码规范 §5）。
 */

import { api } from "$lib/api/client";
import type { TaskRow } from "$lib/api/types";
import { events } from "$lib/stores/events.svelte";

const POLL_MS = 10_000;

/** 任务行读数：字节/速率/剩余。SSE 帧优先，缺帧时回落到 DB 快照。 */
export interface TaskReadings {
  received: number;
  total: number | null;
  speed: number | null;
  eta: number | null;
}

class Queue {
  tasks = $state<TaskRow[]>([]);
  loaded = $state(false);

  #timer: number | undefined = undefined;
  #unsubscribe: (() => void) | null = null;

  /** 某一行当下的读数（下载任务页与仪表盘共用，避免各自拼一遍帧与快照）。 */
  readings(task: TaskRow): TaskReadings {
    const frame = events.progress[task.id];
    return {
      received: frame?.progress_bytes ?? task.progress_bytes,
      total: frame?.total_bytes ?? task.total_bytes,
      speed: frame?.speed ?? task.speed,
      eta: frame?.eta ?? null,
    };
  }

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
