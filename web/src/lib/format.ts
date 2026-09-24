/** 展示格式化：时长、字节、日期（docs/编码规范.md §5 统一 util）。 */

export function formatDuration(sec: number | null | undefined): string {
  if (sec == null || sec < 0 || !Number.isFinite(sec)) return "--:--";
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null || bytes < 0 || !Number.isFinite(bytes)) return "--";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** 传输速率：与 formatSize 同族，后缀 /s。 */
export function formatRate(bytesPerSec: number | null | undefined): string {
  if (bytesPerSec == null || bytesPerSec <= 0) return "--";
  return `${formatSize(bytesPerSec)}/s`;
}

export function formatEta(sec: number | null | undefined): string {
  if (sec == null || sec < 0 || !Number.isFinite(sec)) return "--";
  if (sec < 60) return `${Math.round(sec)} 秒`;
  if (sec < 3600) return `${Math.round(sec / 60)} 分`;
  return `${(sec / 3600).toFixed(1)} 时`;
}

/** 计数（成员数等）：按浏览器语言缩写，大数走紧凑写法（zh「12.8万」/ en「128.4K」）。 */
export function formatCount(n: number | null | undefined): string {
  if (n == null || n < 0 || !Number.isFinite(n)) return "--";
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

/** 运行时长：最多给两档（天+时 / 时+分 / 分），仪表盘的「运行时间」用。 */
export function formatUptime(sec: number | null | undefined): string {
  if (sec == null || sec < 0 || !Number.isFinite(sec)) return "--";
  const total = Math.floor(sec);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分`;
  return `${total} 秒`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

/** 字节进度比例，0..1；总量未知时返回 null（页面据此显示不确定态）。 */
export function progressRatio(done: number | null, total: number | null): number | null {
  if (total == null || total <= 0 || done == null) return null;
  return Math.min(1, Math.max(0, done / total));
}

/** 路径拆成「目录」与「文件名」：列表里目录可截断，文件名（含扩展名）必须始终可读。 */
export function splitPath(path: string): { dir: string; file: string } {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (cut < 0) return { dir: "", file: path };
  return { dir: path.slice(0, cut + 1), file: path.slice(cut + 1) };
}
