/** 播放器：全局唯一实例交给 APlayer（吸底模式），这里只是它的事件桥。
 *
 * 页面与行内按钮只说两件事——「按这份上下文播放第 index 首」与「现在哪首在响」；
 * 进度、音量、循环、顺序与播放列表 UI 全部由 APlayer 自己承担（它的设置存在
 * 自己的 localStorage 键里，与主题/语言一样不写服务端）。挂载点在 PlayerHost：
 * 外壳常驻，切页不销毁，歌照播。
 *
 * aplayer 的 JS 与样式走 import()（见 mount）：它 70 kB（含 12 kB 样式）对首屏毫无
 * 用处，静态引入等于每次冷启动都先垫上这段字节；只有类型是静态 import。
 */

import type APlayer from "aplayer";
import { t } from "$lib/i18n/index.svelte";
import { lyricsUrl } from "$lib/lyrics";
import { theme } from "$lib/stores/theme.svelte";

export interface Track {
  /** 稳定的行标识（history id / chat_id-message_id），用于高亮当前行。 */
  id: string;
  title: string;
  artist: string | null;
  streamUrl: string;
  /** 封面 URL；给了就在 APlayer 的封面位显示，没有则显示主色块。 */
  cover?: string | null;
}

/** 播放器的主题色跟随界面主色（浅/深各一档，取设计令牌 --primary 的原值）。 */
const THEME_COLOR = { light: "#0b7a55", dark: "#4fb08a" } as const;

class Player {
  /** 正在响的那首 Track.id：行高亮据此判断自己是不是当前行。 */
  currentId = $state<string | null>(null);
  playing = $state(false);

  #ap: APlayer | null = null;
  /** 分块在路上（含等待）：这期间收到的点播先排队，见 play / #create。 */
  #loading = false;
  /** 每次 mount 递增；分块回来时对不上号，说明外壳已经卸载过，就别再建实例。 */
  #generation = 0;
  /** 分块没到位时收到的播放意图，实例建好后补播。 */
  #queued: { tracks: Track[]; index: number } | null = null;
  /** 当前列表的上下文：APlayer 的列表只存 url/name，行高亮还得靠 id 对回 Track。 */
  #tracks: Track[] = [];
  /** 盯 APlayer 歌词容器的空/非空：没有歌词时把面板整个收掉（见 mount）。 */
  #lrcObserver: MutationObserver | null = null;

  get current(): Track | null {
    return this.#tracks.find((track) => track.id === this.currentId) ?? null;
  }

  /** PlayerHost 挂载时创建实例；外壳常驻，一次就够。
   *
   * 这里才是 aplayer 的分块入口：先取回 JS 与样式再建实例。分块回来之前外壳已经
   * 画完，首屏不必为播放器等字节；这期间用户点播的意图由 #queued 兜住。
   */
  mount(container: HTMLElement) {
    if (this.#ap || this.#loading) return;
    this.#loading = true;
    const generation = ++this.#generation;
    void this.#create(container, generation);
  }

  async #create(container: HTMLElement, generation: number) {
    const [{ default: APlayer }] = await Promise.all([
      import("aplayer"),
      import("aplayer/dist/APlayer.min.css"),
    ]);
    // 加载期间外壳卸载过（闸门翻面 / 整页导航）：容器已经不作数，别再建实例
    if (generation !== this.#generation) return;
    // 到这里到下面赋 #ap 之间没有 await：不会有 play() 插在「已不 loading 又没有实例」的空档
    this.#loading = false;
    const ap = new APlayer({
      container,
      audio: [],
      fixed: true,
      // 1.10.1 里 fixed 模式的 mini 默认值是 true（mini: narrow || fixed），
      // 不显式关掉的话控制区会被 scaleX(0) 折叠成只剩封面的窄条
      mini: false,
      autoplay: false,
      theme: THEME_COLOR[theme.resolved],
      loop: "all",
      order: "list",
      preload: "metadata",
      volume: 0.7,
      mutex: true,
      // lrcType 3 = 每首 audio.lrc 是一个 URL，APlayer 切歌时自行 XHR 拉取并滚动；
      // URL 由 #replaceList 统一给后端 /api/lyrics（恒 200，无歌词回空体）。
      lrcType: 3,
      listFolded: true,
      // 1.10.1 会把这个值原样内插进 style="max-height: …"：必须带单位，
      // 纯数字会生成非法 CSS 被浏览器丢弃，列表就失去高度上限了。
      listMaxHeight: "420px",
      storageName: "tgm-aplayer",
    });
    this.#ap = ap;

    ap.on("play", () => (this.playing = true));
    ap.on("pause", () => (this.playing = false));
    // loop=all 时下一首的 play 事件会接上，这里只是循环关尽的兜底
    ap.on("ended", () => (this.playing = false));
    ap.on("listswitch", (data) => {
      this.currentId = this.#tracks[data.index ?? -1]?.id ?? null;
    });
    // APlayer 自带的报错是英文写死的：用同一个通知条换成界内文案
    ap.on("error", () => {
      this.playing = false;
      ap.notice(t("player.unplayable"), 3000);
    });

    // APlayer 的 <audio> 默认是游离节点：挂进容器（hidden 不占版面），
    // 带着在播的游离媒体节点做整页导航在部分内嵌内核上会崩掉渲染进程
    ap.audio.hidden = true;
    container.appendChild(ap.audio);

    // fixed 模板出厂给 info 写了内联 display:none，指望 mini:true 的构造分支救回；
    // 我们显式 mini:false 起步就是展开态，把这个内联样式摘掉
    container.querySelector(".aplayer-info")?.removeAttribute("style");

    // 歌词面板的空/非空：APlayer 没有对应事件，盯它的歌词容器自己维护。
    // 有内容才显示面板（空歌词不渲染一张空卡），容器 class 供 CSS 命中。
    const lrcContents = container.querySelector(".aplayer-lrc-contents");
    if (lrcContents) {
      const sync = () =>
        container.classList.toggle("aplayer-lrc-empty", lrcContents.children.length === 0);
      this.#lrcObserver = new MutationObserver(sync);
      this.#lrcObserver.observe(lrcContents, { childList: true });
      sync();
    }

    // 分块在飞时点的播：实例已就绪，补上（#create 里最后一个 await 之后没有插队的机会）
    const queued = this.#queued;
    this.#queued = null;
    if (queued) this.play(queued.tracks, queued.index);
  }

  unmount() {
    this.#generation += 1; // 作废在飞的分块加载
    this.#loading = false;
    this.#queued = null;
    this.#lrcObserver?.disconnect();
    this.#lrcObserver = null;
    this.#ap?.destroy();
    this.#ap = null;
    this.#tracks = [];
    this.currentId = null;
    this.playing = false;
  }

  /** 播放入口：行内播放键 / 试听 / 播放所选都汇到这一个方法。
   *
   * - 上下文（列表）换了就整列重建；
   * - 点的是当前正在响的那首：按钮此刻就是「暂停」，只暂停，不从头重放；
   * - 其余一律从这首的开头播。
   */
  play(tracks: Track[], index: number) {
    const track = tracks[index];
    if (!track) return;

    const ap = this.#ap;
    if (!ap) {
      // 分块还在路上（冷启动后立刻点播/试听）：记下意图，实例建好后补播。
      // 外壳已卸载（压根没有播放器）时照旧丢弃——与静态引入时同一行为。
      if (this.#loading) this.#queued = { tracks, index };
      return;
    }

    if (track.id === this.currentId && this.playing) {
      ap.pause();
      return;
    }

    if (!this.#sameContext(tracks)) {
      this.#replaceList(ap, tracks);
    }

    const target = this.#tracks.findIndex((item) => item.id === track.id);
    if (target < 0) return;
    if (target === ap.list.index) {
      ap.seek(0);
    } else {
      ap.list.switch(target);
    }
    ap.play();
    this.currentId = track.id;
    this.playing = true;
  }

  /** 页面卸载（pagehide）时调用：先把在播的音频停住。
   *  APlayer 的 audio 元素是游离节点，带着「正在播」的状态做整页导航
   *  会把渲染进程挂死——卸载前暂停是兜底。 */
  pause() {
    this.#ap?.pause();
  }

  /** 主题切换时把主色刷进封面块 / 进度条 / 列表游标。 */
  applyTheme() {
    const color = THEME_COLOR[theme.resolved];
    const ap = this.#ap;
    if (!ap) return;
    for (let index = 0; index < ap.list.audios.length; index += 1) {
      ap.theme(color, index);
    }
  }

  #sameContext(tracks: Track[]): boolean {
    return (
      this.#tracks.length === tracks.length &&
      tracks.every((track, i) => track.id === this.#tracks[i]?.id)
    );
  }

  #replaceList(ap: APlayer, tracks: Track[]) {
    this.#tracks = tracks;
    ap.list.clear();
    ap.list.add(
      tracks.map((track) => ({
        name: track.title,
        artist: track.artist ?? "",
        url: track.streamUrl,
        cover: track.cover ?? undefined,
        // 歌词走后端代理：恒 200，查不到空体 → APlayer 解析为空，面板留白
        lrc: lyricsUrl(track.title, track.artist),
        theme: THEME_COLOR[theme.resolved],
      })),
    );
  }
}

export const player = new Player();
