/** 外观偏好：跟随系统 / 浅色 / 深色（FR-WEB-06）。
 *
 * 自带实现，不引 mode-watcher：它的 modeStorageKey 在模块导入期就被固定为默认键，
 * 自定义键在读回时失效（表现为刷新后主题总是回到「跟随系统」）。
 * 首帧防闪白在 index.html 的内联脚本里做，与本文件共用 `tgm-theme` 这一键。
 */

export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "tgm-theme";

export const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "app.themeSystem" },
  { value: "light", label: "app.themeLight" },
  { value: "dark", label: "app.themeDark" },
];

class Theme {
  preference = $state<ThemePreference>(readStoredPreference());
  /** 系统是否为深色；由 matchMedia 驱动。初值就地取一次（start() 里再订阅变化）——
   *  留 `false` 等 start() 纠正的话，首帧会先按浅色落一次：跟随系统 + 深色系统下，
   *  index.html 的内联脚本已经铺了深色，挂载却把它翻回浅色、再由 start() 翻回去，
   *  一开屏就是一闪（v3.26 起这段闪还会被主题过渡放大成一次可见的淡入）。 */
  systemDark = $state(window.matchMedia("(prefers-color-scheme: dark)").matches);

  #media: MediaQueryList | null = null;
  #onChange = () => {
    this.systemDark = this.#media?.matches ?? false;
  };

  get resolved(): "light" | "dark" {
    if (this.preference === "system") return this.systemDark ? "dark" : "light";
    return this.preference;
  }

  /** 订阅系统主题；App 挂载时调用一次。 */
  start() {
    if (this.#media) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    this.#media = media;
    this.systemDark = media.matches;
    media.addEventListener("change", this.#onChange);
  }

  set(next: ThemePreference) {
    this.preference = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 隐私模式：只在本会话生效
    }
  }
}

function readStoredPreference(): ThemePreference {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // 读不到就用默认
  }
  return "system";
}

export const theme = new Theme();
