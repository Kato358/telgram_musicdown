/** SSE 唯一订阅点（编码规范 §5）：组件只消费，不各自 new EventSource。 */

import type { LogErrorEvent, TaskProgressEvent, TaskStatusEvent } from "$lib/api/types";

export type StatusListener = (event: TaskStatusEvent) => void;

class Events {
  connected = $state(false);
  /** 按 task_id 索引的最新进度帧（含 speed/eta）。 */
  progress = $state<Record<number, TaskProgressEvent>>({});
  /** 按 task_id 索引的最新状态帧。 */
  statuses = $state<Record<number, TaskStatusEvent>>({});
  /** 连接期间收到的错误事件（日志页消费）。 */
  errors = $state<(LogErrorEvent & { id: number })[]>([]);
  /** 状态事件计数：页面用 $effect 观察它触发重载（DB 才是事实源）。 */
  revision = $state(0);

  #source: EventSource | null = null;
  #listeners = new Set<StatusListener>();
  #errorSeq = 0;

  connect() {
    if (this.#source) return;
    const source = new EventSource("/api/events");
    source.onopen = () => {
      this.connected = true;
    };
    source.onerror = () => {
      this.connected = false;
    };
    source.onmessage = (frame) => {
      let parsed: { type?: string; payload?: Record<string, unknown> };
      try {
        parsed = JSON.parse(frame.data) as typeof parsed;
      } catch {
        return;
      }
      if (parsed.type === "task.progress" && parsed.payload) {
        const p = parsed.payload as unknown as TaskProgressEvent;
        this.progress[p.task_id] = p;
      } else if (parsed.type === "task.status" && parsed.payload) {
        const s = parsed.payload as unknown as TaskStatusEvent;
        this.statuses[s.task_id] = s;
        this.revision += 1;
        for (const listener of this.#listeners) listener(s);
      } else if (parsed.type === "log.error" && parsed.payload) {
        this.errors = [
          ...this.errors,
          {
            id: (this.#errorSeq += 1),
            message: String(parsed.payload.message ?? ""),
            ts: Number(parsed.payload.ts ?? Date.now() / 1000),
          },
        ];
      }
    };
    this.#source = source;
  }

  onStatus(listener: StatusListener): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  /** 任务离开进行中状态时丢掉它的进度帧，避免行上残留旧速率。 */
  forget(taskId: number) {
    const next = { ...this.progress };
    delete next[taskId];
    this.progress = next;
  }

  clearErrors() {
    this.errors = [];
  }

  disconnect() {
    this.#source?.close();
    this.#source = null;
    this.connected = false;
  }
}

export const events = new Events();
