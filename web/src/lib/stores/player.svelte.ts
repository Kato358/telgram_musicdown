/** 播放器状态（FR-PLAY-01/03）：全局唯一 `<audio>` 挂在 TransportBar。
 *
 * 队列上下文 = 历史筛选结果或搜索结果的已可播项；同一时刻一首。
 * 进度外观偏好存本机浏览器（`tgm-player-progress`），与主题/语言同为「不写服务端」的界面偏好。
 */

export interface Track {
  /** 稳定的行标识（history id / chat_id-message_id），用于高亮当前行。 */
  id: string;
  title: string;
  artist: string | null;
  streamUrl: string;
}

/** 进度条两种形式：线型（默认）与波形，同一 range 内核只换皮肤。 */
export type ProgressStyle = "line" | "wave";

const PROGRESS_KEY = "tgm-player-progress";

export const PROGRESS_STYLES: { value: ProgressStyle; label: string }[] = [
  { value: "line", label: "player.styleLine" },
  { value: "wave", label: "player.styleWave" },
];

function readStoredStyle(): ProgressStyle {
  try {
    return localStorage.getItem(PROGRESS_KEY) === "wave" ? "wave" : "line";
  } catch {
    return "line";
  }
}

class Player {
  queue = $state<Track[]>([]);
  index = $state(-1);
  playing = $state(false);
  buffering = $state(false);
  currentTime = $state(0);
  duration = $state(0);
  volume = $state(1);
  error = $state<string | null>(null);
  progressStyle = $state<ProgressStyle>(readStoredStyle());
  shuffle = $state(false);
  /** 循环：队尾之后回到队首，不重复单曲。 */
  repeat = $state(false);

  get current(): Track | null {
    return this.queue[this.index] ?? null;
  }

  get hasPrev(): boolean {
    return this.index > 0 || (this.repeat && this.queue.length > 1);
  }

  get hasNext(): boolean {
    return this.index >= 0 && (this.index < this.queue.length - 1 || (this.repeat && this.queue.length > 1));
  }

  get ratio(): number {
    if (this.duration <= 0) return 0;
    return Math.min(1, Math.max(0, this.currentTime / this.duration));
  }

  play(tracks: Track[], index: number) {
    this.queue = tracks;
    this.index = index;
    this.#resetFrom(0);
    this.playing = true;
  }

  pause() {
    this.playing = false;
  }

  resume() {
    if (this.current) {
      this.error = null;
      this.playing = true;
    }
  }

  toggle() {
    if (this.playing) this.pause();
    else this.resume();
  }

  /** 下一首：随机优先，其次顺延，再次（开了循环）回到队首；都没有就停在末尾。 */
  next() {
    if (this.queue.length === 0) return;
    if (this.shuffle && this.queue.length > 1) {
      this.index = this.#randomIndex();
    } else if (this.index < this.queue.length - 1) {
      this.index += 1;
    } else if (this.repeat) {
      this.index = 0;
    } else {
      this.playing = false;
      this.currentTime = this.duration;
      return;
    }
    this.#resetFrom(0);
    this.playing = true;
  }

  /** 上一首：播放已超过 3 秒时先回到本曲开头（与原生播放器手感一致）。 */
  prev() {
    if (!this.current) return;
    if (this.currentTime > 3) {
      this.currentTime = 0;
      return;
    }
    if (this.index > 0) {
      this.index -= 1;
    } else if (this.repeat && this.queue.length > 1) {
      this.index = this.queue.length - 1;
    } else {
      this.currentTime = 0;
      return;
    }
    this.#resetFrom(0);
    this.playing = true;
  }

  stop() {
    this.queue = [];
    this.index = -1;
    this.playing = false;
    this.buffering = false;
    this.currentTime = 0;
    this.duration = 0;
    this.error = null;
  }

  seekRatio(ratio: number) {
    if (this.duration > 0) {
      this.currentTime = Math.min(this.duration, Math.max(0, ratio * this.duration));
    }
  }

  setVolume(next: number) {
    this.volume = Math.min(1, Math.max(0, next));
  }

  setProgressStyle(next: ProgressStyle) {
    this.progressStyle = next;
    try {
      localStorage.setItem(PROGRESS_KEY, next);
    } catch {
      // 隐私模式：只在本会话生效
    }
  }

  fail(message: string) {
    this.playing = false;
    this.buffering = false;
    this.error = message;
  }

  #resetFrom(seconds: number) {
    this.currentTime = seconds;
    this.duration = 0;
    this.error = null;
    this.buffering = false;
  }

  #randomIndex(): number {
    if (this.queue.length < 2) return this.index;
    let next = this.index;
    while (next === this.index) {
      next = Math.floor(Math.random() * this.queue.length);
    }
    return next;
  }
}

export const player = new Player();
