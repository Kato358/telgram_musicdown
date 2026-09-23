/** i18n：自带实现，不引第三方库；locale 存本机（FR-WEB-05）。
 *
 * 组件内禁止硬编码文案：一律 `t("a.b")`。缺键回落到英文，再回落显示键名（便于发现漏配）。
 */

import { en, zhCN, type Dict } from "./messages";

export type Locale = "zh-CN" | "en";

export const LOCALES: { value: Locale; label: string }[] = [
  { value: "zh-CN", label: "简体中文" },
  { value: "en", label: "English" },
];

const STORAGE_KEY = "tgm-locale";
const DICTS: Record<Locale, Dict> = { "zh-CN": zhCN, en };

function readStoredLocale(): Locale {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "en" || raw === "zh-CN") return raw;
  } catch {
    // 隐私模式下读取失败：用默认
  }
  return "zh-CN";
}

function lookup(dict: Dict, key: string): string | null {
  let node: Dict | string = dict;
  for (const part of key.split(".")) {
    if (typeof node === "string") return null;
    const next: string | Dict | undefined = node[part];
    if (next === undefined) return null;
    node = next;
  }
  return typeof node === "string" ? node : null;
}

class I18n {
  locale = $state<Locale>(readStoredLocale());

  setLocale(next: Locale) {
    this.locale = next;
    document.documentElement.lang = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 存不下就只在本次会话生效
    }
  }

  t(key: string, params?: Record<string, string | number>): string {
    const raw = lookup(DICTS[this.locale], key) ?? lookup(DICTS.en, key) ?? key;
    if (!params) return raw;
    return raw.replace(/\{(\w+)\}/g, (match, name: string) =>
      name in params ? String(params[name]) : match,
    );
  }
}

export const i18n = new I18n();

export function t(key: string, params?: Record<string, string | number>): string {
  return i18n.t(key, params);
}

/** 状态枚举文案：`t("tasks.status.downloading")`，未知状态原样显示。 */
export function statusText(status: string): string {
  const key = `tasks.status.${status}`;
  const text = i18n.t(key);
  return text === key ? status : text;
}

/** 任务类型文案（link/bot/sync/preview/search）；未知类型原样显示，不假装认识。 */
export function taskTypeText(type: string): string {
  const key = `tasks.type.${type}`;
  const text = i18n.t(key);
  return text === key ? type : text;
}
