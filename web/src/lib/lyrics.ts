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
