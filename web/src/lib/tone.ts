/** 状态语义的唯一映射：后端任务状态 → 灯色 / 文字色 / 状态词（设计规范 §8）。
 *
 * 三张表共用同一 key 集合，页面不得自行发明颜色或状态词；状态一律「灯 + 词」，
 * 禁止仅用颜色表达状态。
 */

import type { TaskStatus } from "$lib/api/types";
import { t } from "$lib/i18n/index.svelte";

export type Tone = "live" | "done" | "fail" | "wait" | "idle";

/** 灯色（8px 圆点）。 */
export const TONE_DOT: Record<Tone, string> = {
  live: "bg-lamp-live",
  done: "bg-lamp-done",
  fail: "bg-lamp-fail",
  wait: "bg-lamp-wait",
  idle: "bg-lamp-idle",
};

/** 文字色。失败态用 --destructive-text（4.98:1），不用只达 3.7:1 的 --destructive。 */
export const TONE_TEXT: Record<Tone, string> = {
  live: "text-lamp-live",
  done: "text-lamp-done",
  fail: "text-destructive-text",
  wait: "text-lamp-wait",
  idle: "text-muted-foreground",
};

/** 行内提示（Note）左侧 2px 色条。 */
export const TONE_BAR: Record<Tone, string> = {
  live: "border-l-lamp-live",
  done: "border-l-lamp-done",
  fail: "border-l-lamp-fail",
  wait: "border-l-lamp-wait",
  idle: "border-l-lamp-idle",
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

/** 状态词：`t("tasks.status.downloading")`，未知状态原样显示。 */
export function statusText(status: string): string {
  const key = `tasks.status.${status}`;
  const text = t(key);
  return text === key ? status : text;
}

/** 日志级别 → 语义色（日志页）：ERROR/CRITICAL 红、WARNING 黄、其余中性。 */
export function logLevelTone(level: string): Tone {
  const up = level.toUpperCase();
  if (up === "ERROR" || up === "CRITICAL") return "fail";
  if (up === "WARNING") return "wait";
  return "idle";
}
