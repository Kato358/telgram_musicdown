/** 播放器：全局唯一实例交给 APlayer（吸底模式），这里只是它的事件桥。
 *
 * 页面与行内按钮只说两件事——「按这份上下文播放第 index 首」与「现在哪首在响」；
 * 进度、音量、循环、顺序与播放列表 UI 全部由 APlayer 自己承担（它的设置存在
 * 自己的 localStorage 键里，与主题/语言一样不写服务端）。挂载点在 PlayerHost：
 * 外壳常驻，切页不销毁，歌照播。
 *
 * 沉浸全屏（§5.4）也是这一个实例：它只把 APlayer 的音视频元素、列表与歌词搬到
 * 一屏之上，播放本身不搬家——开、关这一层都不碰播放状态。本 store 因此多担两件事：
 * 把音频元素的事实（时间 / 时长 / 音量）翻成响应式状态，并按当前曲目取一份歌词
 * （APlayer 自己那份只供它内置的单行歌词条，没有时间轴，沉浸层要的是可点的整篇）。
 *
 * aplayer 的 JS 与样式走 import()（见 mount）：它 70 kB（含 12 kB 样式）对首屏毫无
 * 用处，静态引入等于每次冷启动都先垫上这段字节；只有类型是静态 import。
 */

import type APlayer from "aplayer";
import { t } from "$lib/i18n/index.svelte";
import { lyricsUrl, parseLrc, type LrcLine } from "$lib/lyrics";
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

/** 播放模式：把 APlayer 的 `order × loop` 折算成用户能理解的一档（见 cycleMode）。
 *  list = 列表循环、one = 单曲循环、random = 随机播放。 */
export type PlayMode = "list" | "one" | "random";

/** 沉浸入口图标（四角展开）：注进 APlayer 控制排的那个按钮用，见 #injectImmersive。 */
const IMMERSIVE_ICON =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>';

/** APlayer 的列表事件（listswitch 等）带 `{ index }`；音频事件转发的是原生 Event，
 *  没有下标。同一个 `on` 通道混着两类载荷，这里按形状收窄，取不到就是没有列表。 */
function listIndex(data: { index?: number } | Event): number {
  if ("index" in data && typeof data.index === "number") return data.index;
  return -1;
}

class Player {
  /** 播放器当前挂着的那首 Track.id：暂停后仍然指着它（播放条还要显示它）。
   *  单看这个字段判断不出「正在响」——那是 playing 的事，行件一律走 isPlaying。 */
  currentId = $state<string | null>(null);
  /** 音频元素此刻在响（`play` / `pause` / `ended` / `error` 事件的事实，不乐观置位）。 */
  playing = $state(false);
  /** 当前列表的上下文：APlayer 的列表只存 url/name，行的 id 与封面得靠下标对回 Track
   *  （listswitch 换算 currentId、点播时找目标下标、判断上下文是否同一份、沉浸层的歌单）。 */
  queue = $state<Track[]>([]);
  /** 正在响的那首在 queue 里的下标；没有列表时为 -1。 */
  index = $state(-1);
  /** 当前曲目：沉浸层的封面 / 歌名 / 歌词都以它为源。 */
  current = $derived(this.queue[this.index] ?? null);
  /** 音频元素的事实（timeupdate / durationchange / volumechange 转发），供沉浸层画进度与音量。 */
  time = $state(0);
  duration = $state(0);
  volume = $state(0.7);
  /** 沉浸全屏是否展开。只开关这一层，不碰播放。 */
  immersive = $state(false);
  /** 当前曲目的歌词（LRC 解析后，按时间升序）；空数组 = 没有歌词。 */
  lyrics = $state<LrcLine[]>([]);
  /** 播放模式当前档位，由 APlayer 的 order/loop 折出（见 cycleMode）。 */
  mode = $state<PlayMode>("list");

  #ap: APlayer | null = null;
  /** APlayer 的宿主元素：注沉浸按钮、找它的模式按钮都要从这里查。 */
  #container: HTMLElement | null = null;
  /** 分块在路上（含等待）：这期间收到的点播先排队，见 play / #create。 */
  #loading = false;
  /** 每次 mount 递增；分块回来时对不上号，说明外壳已经卸载过，就别再建实例。 */
  #generation = 0;
  /** 分块没到位时收到的播放意图，实例建好后补播。 */
  #queued: { tracks: Track[]; index: number } | null = null;
  /** 歌词按 Track.id 缓存：一首歌只取一次，切回来直接用。 */
  #lyricsCache = new Map<string, LrcLine[]>();
  /** 每次取歌词递增；回来时对不上号说明已经切歌，丢弃这次结果。 */
  #lyricsToken = 0;
  /** 盯 APlayer 歌词容器的空/非空：没有歌词时把面板整个收掉（见 mount）。 */
  #lrcObserver: MutationObserver | null = null;

  /** 这一行此刻是不是**正在响**：行高亮与播放键图标/词共用这一条判据。
   *
   *  只看 currentId 是不够的：暂停、播完、播放失败之后它仍然指着刚才那首，
   *  行件会一直画成「播放中」，与音频的真实状态对不上。 */
  isPlaying(id: string): boolean {
    return this.playing && this.currentId === id;
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
    this.#container = container;

    // 起始态是收起的（v3.26）：没有在响的东西时，机身就只是一枚 86×68 的封面片，
    // 不占版面、不抢视线；第一次真播起来才展开（下一行的 play 桥）。
    // 收起 / 展开是 APlayer 自己的状态，一律走 setMode——折叠把手按钮也走同一条路，
    // 图标与「机身 / 列表」的显隐因此不会和我们的判断脱节。
    ap.setMode("mini");

    // playing 只在这里被写：四个事件都是音频元素的事实（APlayer 的 audioEvents 直接转发
    // DOM 事件），所以行件的「播放中」画的是真在响，不是「点过播放」。
    ap.on("play", () => {
      this.playing = true;
      // 「在响」与「展开」同一刻：收起态下点封面上的播放键也算起播（用户自己收起的
      // 机身也该在这一刻打开）。已在展开态就不动它，免得与折叠把手抢同一件事。
      if (ap.mode !== "normal") ap.setMode("normal");
    });
    ap.on("pause", () => (this.playing = false));
    // loop=all 时下一首的 play 事件会接上，这里只是循环关尽的兜底
    ap.on("ended", () => (this.playing = false));
    ap.on("listswitch", (data) => {
      this.#setCurrent(listIndex(data));
    });
    // 音频元素的事实，供沉浸层画进度与音量：与 playing 同一口径——从元素转发，不乐观置位。
    ap.on("timeupdate", () => (this.time = ap.audio.currentTime));
    ap.on("durationchange", () => (this.duration = ap.duration));
    ap.on("volumechange", () => (this.volume = ap.audio.volume));
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

    // 沉浸入口注进 APlayer 的控制排（出厂没有这个键，见 #injectImmersive）
    this.#injectImmersive(container);

    // 起步读数：音量是 APlayer 从它自己的 localStorage 键里恢复的，这里照元素的实际值取，
    // 不用构造参数里的 0.7（那只是首次无存档时的初值）。
    this.time = ap.audio.currentTime;
    this.duration = ap.duration;
    this.volume = ap.audio.volume;
    this.mode = this.#readMode();

    // 分块在飞时点的播：实例已就绪，补上（#create 里最后一个 await 之后没有插队的机会）
    const queued = this.#queued;
    this.#queued = null;
    if (queued) this.play(queued.tracks, queued.index);
  }

  /** 沉浸入口：APlayer 的控制排没有全屏键，往 `.aplayer-time` 里注一个自己的按钮。
   *
   *  它只开这一层，不改播放；用真 `<button>` 而不是 APlayer 那种 `<span>`，键盘可达。
   *  APlayer 的图标样式是 `.aplayer-icon path`，这里沿用同名类让配色与邻键一致。 */
  #injectImmersive(container: HTMLElement) {
    const row = container.querySelector(".aplayer-time");
    if (!row) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "aplayer-icon aplayer-immersive";
    button.innerHTML = IMMERSIVE_ICON;
    const label = t("player.immersive.enter");
    button.title = label;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", () => this.openImmersive());
    row.appendChild(button);
  }

  /** 切到列表第 i 首：写下标、换 currentId、按曲目取歌词——listswitch 与点播共用这一处。 */
  #setCurrent(index: number) {
    this.index = index;
    const track = this.queue[index] ?? null;
    this.currentId = track?.id ?? null;
    void this.#loadLyrics(track);
  }

  /** 取当前曲目的歌词（按 id 缓存）。
   *
   *  后端恒 200：没有时间轴 / 查不到一律回空体，解析出空数组就按「无歌词」渲染。
   *  只有真取到了才写缓存——网络抖一次不该把这首歌永久钉成没词。 */
  async #loadLyrics(track: Track | null) {
    const token = ++this.#lyricsToken;
    if (!track) {
      this.lyrics = [];
      return;
    }
    const cached = this.#lyricsCache.get(track.id);
    if (cached) {
      this.lyrics = cached;
      return;
    }
    this.lyrics = [];
    try {
      const response = await fetch(lyricsUrl(track.title, track.artist));
      if (!response.ok) return;
      const lines = parseLrc(await response.text());
      this.#lyricsCache.set(track.id, lines);
      if (token === this.#lyricsToken) this.lyrics = lines;
    } catch {
      // 取词失败就留空面板：歌词是装饰，不弹错、不阻塞播放
    }
  }

  /** 沉浸全屏开关：只开关这一层，播放照旧。 */
  openImmersive() {
    this.immersive = true;
  }

  closeImmersive() {
    this.immersive = false;
  }

  /** 播放 / 暂停：只有一个写入口，图标与行高亮都跟着音频事件走。 */
  toggle() {
    const ap = this.#ap;
    if (!ap) return;
    if (this.playing) ap.pause();
    else ap.play();
  }

  /** 上一首 / 下一首：交给 APlayer 自己的 prevIndex/nextIndex，随机顺序下也对。 */
  prev() {
    this.#ap?.skipBack();
  }

  next() {
    this.#ap?.skipForward();
  }

  /** 跳转 / 音量：都走 APlayer 自己的方法，它的进度条与音量图标跟着一起动。 */
  seek(seconds: number) {
    this.#ap?.seek(seconds);
  }

  setVolume(value: number) {
    this.#ap?.volume(value);
  }

  /** 歌单点播：与 APlayer 自己的列表点击同一语义——同一首就切播放态，别的就切过去播。 */
  playAt(index: number) {
    const ap = this.#ap;
    if (!ap) return;
    if (index === this.index) {
      this.toggle();
      return;
    }
    ap.list.switch(index);
    ap.play();
  }

  /** 切档：列表循环 → 单曲循环 → 随机播放 → 列表循环。
   *
   *  order / loop 的唯一所有者是 APlayer——它自己的模式按钮就地改 `options` 并按状态换图标。
   *  这里**点它的那两个按钮**逼近目标态，而不是直接改 `options`：直接改会让退出沉浸后
   *  露出的 APlayer 图标与真实状态对不上。单曲循环再点一下就是随机（loop 回 all + order 转 random）。 */
  cycleMode() {
    const container = this.#container;
    if (!container) return;
    const loopButton = container.querySelector<HTMLElement>(".aplayer-icon-loop");
    const orderButton = container.querySelector<HTMLElement>(".aplayer-icon-order");
    if (this.mode === "random") {
      this.#clickUntilLoop(loopButton, "all");
      this.#clickUntilOrder(orderButton, "list");
    } else if (this.mode === "one") {
      this.#clickUntilLoop(loopButton, "all");
      this.#clickUntilOrder(orderButton, "random");
    } else {
      this.#clickUntilLoop(loopButton, "one");
    }
    this.mode = this.#readMode();
  }

  /** loop 是三档循环（all → one → none），点到目标档为止；单曲时 APlayer 会忽略点击，
   *  所以加个次数上限，点不动就保持原样（列表只有一首时模式本来也切不动）。 */
  #clickUntilLoop(button: HTMLElement | null, target: "all" | "one" | "none") {
    for (let i = 0; i < 3 && button && this.#ap?.options.loop !== target; i += 1) button.click();
  }

  /** order 是两档对切，一次点击到位。 */
  #clickUntilOrder(button: HTMLElement | null, target: "list" | "random") {
    if (button && this.#ap?.options.order !== target) button.click();
  }

  /** 从 APlayer 的 order/loop 折出当前档位。 */
  #readMode(): PlayMode {
    const options = this.#ap?.options;
    if (!options) return "list";
    if (options.order === "random") return "random";
    return options.loop === "one" ? "one" : "list";
  }

  unmount() {
    this.#generation += 1; // 作废在飞的分块加载
    this.#loading = false;
    this.#queued = null;
    this.#lrcObserver?.disconnect();
    this.#lrcObserver = null;
    this.#ap?.destroy();
    this.#ap = null;
    this.#container = null;
    this.queue = [];
    this.index = -1;
    this.currentId = null;
    this.playing = false;
    this.immersive = false;
    this.lyrics = [];
    this.#lyricsCache.clear();
    this.#lyricsToken += 1;
  }

  /** 播放入口：行内播放键 / 播放所选都汇到这一个方法。
   *
   * - 点的是当前这一首（同一个 id = 同一份文件，下载页与曲库页共用它）：只是暂停 / 续播——
   *   不重建列表、不 seek(0)。进度是同一份，重建列表只会把同一个文件从头再拉一遍；
   * - 换了别的轨：上下文（列表）换了就整列重建，然后从这首的开头播。
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

    if (track.id === this.currentId) {
      if (this.playing) ap.pause();
      else ap.play(); // 续播：不 seek、不换 src（此刻元素里就是这份文件）
      return;
    }

    if (!this.#sameContext(tracks)) {
      this.#replaceList(ap, tracks);
    }

    const target = this.queue.findIndex((item) => item.id === track.id);
    if (target < 0) return;
    if (target === ap.list.index) {
      ap.seek(0);
    } else {
      ap.list.switch(target);
    }
    ap.play();
    this.#setCurrent(target);
    // 这里不动 playing：状态只由音频元素的事件置位（见 mount 的事件桥）。
    // 乐观置位过的话，浏览器拒绝播放（NotAllowedError，APlayer 自己吞掉只改它自己的按键）
    // 或流打不开时，行键会先画成「暂停」——那正是「按键状态与实际播放不一致」的另一半。
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
      this.queue.length === tracks.length &&
      tracks.every((track, i) => track.id === this.queue[i]?.id)
    );
  }

  #replaceList(ap: APlayer, tracks: Track[]) {
    this.queue = tracks;
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
