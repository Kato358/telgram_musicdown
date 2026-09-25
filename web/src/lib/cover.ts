/** 全局封面 URL（后端链路：本地标签 > api.lrc.cx > 无封面）。
 *
 * 所有封面入口（搜索行、下载行、播放器队列、飞片动画）共用这一条链路；
 * title/artist 都缺省时不给 URL（请求必然 404），调用方直接退音符占位。
 */
export function coverUrl(
  title: string | null | undefined,
  artist: string | null | undefined,
): string | null {
  const t = title?.trim();
  const a = artist?.trim();
  if (!t && !a) return null;
  const params = new URLSearchParams();
  if (t) params.set("title", t);
  if (a) params.set("artist", a);
  return `/api/cover?${params.toString()}`;
}
