/** 全局歌词 URL（后端代理 api.lrc.cx/lyrics；接口无 CORS 头，浏览器不能直连）。
 *
 * 播放器歌词面板共用这一条链路。后端恒 200——查不到回空体，APlayer 解析出
 * 空列表，面板静默留白（不会弹它内置的英文报错）。
 */
export function lyricsUrl(
  title: string | null | undefined,
  artist: string | null | undefined,
): string {
  const params = new URLSearchParams();
  const t = title?.trim();
  const a = artist?.trim();
  if (t) params.set("title", t);
  if (a) params.set("artist", a);
  return `/api/lyrics?${params.toString()}`;
}

export interface LrcLine {
  /** 这一行开始的秒数。 */
  time: number;
  text: string;
}

/** 时间标签：`[mm:ss]` / `[mm:ss.xx]` / `[mm:ss:xx]`，一行可以挂多个。 */
const LRC_TIME = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;

/** 解析 LRC 文本。一行挂多个时间标签（合唱 / 重复段）时逐条展开；元数据标签
 *  （`[ar:]` `[ti:]` `[offset:]` 这类没有时间轴的行）与纯时间轴的间奏行不产出条目——
 *  面板画的是「要唱的词」，空行只会让滚动条多跳一格。
 *
 *  返回按时间升序排列的条目；一段带时间轴的词都没有时返回空数组（调用方按「无歌词」处理）。 */
export function parseLrc(text: string): LrcLine[] {
  const lines: LrcLine[] = [];
  for (const raw of text.split(/\r?\n/)) {
    LRC_TIME.lastIndex = 0;
    const stamps: number[] = [];
    let match: RegExpExecArray | null;
    while ((match = LRC_TIME.exec(raw)) !== null) {
      const minutes = Number(match[1]);
      const seconds = Number(match[2]);
      // 小数位按毫秒补齐：`.5` = 500ms、`.05` = 50ms
      const millis = match[3] ? Number(match[3].padEnd(3, "0")) / 1000 : 0;
      stamps.push(minutes * 60 + seconds + millis);
    }
    if (stamps.length === 0) continue;
    const body = raw.replace(LRC_TIME, "").trim();
    if (!body) continue;
    for (const time of stamps) lines.push({ time, text: body });
  }
  lines.sort((a, b) => a.time - b.time);
  return lines;
}
