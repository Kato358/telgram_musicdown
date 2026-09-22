/** SSE 订阅唯一入口（编码规范 §5：SSE 只在 stores/events.ts 一处）。 */

import { defineStore } from "pinia";

export interface TaskProgressEvent {
  task_id: number;
  progress_bytes: number;
  total_bytes: number | null;
  speed: number | null;
  eta: number | null;
}

export interface TaskStatusEvent {
  task_id: number;
  status: string;
  error: string | null;
}

interface EventsState {
  source: EventSource | null;
  taskProgress: Record<number, TaskProgressEvent>;
  taskStatus: Record<number, TaskStatusEvent>;
  errors: { message: string; ts: number }[];
  connected: boolean;
}

export const useEventsStore = defineStore("events", {
  state: (): EventsState => ({
    source: null,
    taskProgress: {},
    taskStatus: {},
    errors: [],
    connected: false,
  }),
  actions: {
    /** 连接 SSE（SDD §1.4 事件流）。重复调用安全：已有连接则跳过。 */
    connect() {
      if (this.source) return;
      const es = new EventSource("/api/events");
      es.onopen = () => {
        this.connected = true;
      };
      es.onerror = () => {
        this.connected = false;
      };
      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as { type: string; payload: Record<string, unknown> };
          this.dispatch(data.type, data.payload);
        } catch {
          // 非 JSON 帧，忽略
        }
      };
      this.source = es;
    },
    dispatch(type: string, payload: Record<string, unknown>) {
      if (type === "task.progress") {
        const p = payload as unknown as TaskProgressEvent;
        this.taskProgress[p.task_id] = p;
      } else if (type === "task.status") {
        const s = payload as unknown as TaskStatusEvent;
        this.taskStatus[s.task_id] = s;
      } else if (type === "log.error") {
        this.errors.push({ message: String(payload.message ?? ""), ts: Date.now() });
      }
    },
    disconnect() {
      this.source?.close();
      this.source = null;
      this.connected = false;
    },
  },
});
