/** 状态色调的唯一映射：后端任务状态 → 灯色（设计规范 §5）。
 *
 * 灯色只表达状态，不用于装饰；三张表共用同一 key 集合，避免各页面自行发明颜色。
 */

import type { TaskStatus } from "$lib/api/types";

export type Tone = "live" | "done" | "fail" | "wait" | "idle";

export const TONE_DOT: Record<Tone, string> = {
  live: "bg-lamp-live",
  done: "bg-lamp-done",
  fail: "bg-lamp-fail",
  wait: "bg-lamp-wait",
  idle: "bg-lamp-idle",
};

export const TONE_TEXT: Record<Tone, string> = {
  live: "text-lamp-live",
  done: "text-lamp-done",
  fail: "text-lamp-fail",
  wait: "text-lamp-wait",
  idle: "text-muted-foreground",
};

export const TONE_FILL: Record<Tone, string> = {
  live: "bg-meter-on",
  done: "bg-lamp-done",
  fail: "bg-lamp-fail",
  wait: "bg-lamp-wait",
  idle: "bg-lamp-idle",
};

const STATUS_TONE: Record<TaskStatus, Tone> = {
  queued: "idle",
  downloading: "live",
  paused: "wait",
  success: "done",
  failed: "fail",
  skipped: "done",
  cancelled: "idle",
};

export function taskTone(status: string): Tone {
  return STATUS_TONE[status as TaskStatus] ?? "idle";
}
