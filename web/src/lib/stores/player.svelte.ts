/** 播放器状态（FR-PLAY-01/03）：全局唯一 `<audio>` 挂在 TransportBar。
 *
 * 队列上下文 = 历史筛选结果或搜索结果的已可播项；同一时刻一首。
 */

export interface Track {
  /** 稳定的行标识（history id / chat_id-message_id），用于高亮当前行。 */
  id: string;
  title: string;
  artist: string | null;
  streamUrl: string;
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

  get current(): Track | null {
    return this.queue[this.index] ?? null;
  }

  get hasPrev(): boolean {
    return this.index > 0;
  }

  get hasNext(): boolean {
    return this.index >= 0 && this.index < this.queue.length - 1;
  }

  get ratio(): number {
    if (this.duration <= 0) return 0;
    return Math.min(1, Math.max(0, this.currentTime / this.duration));
  }

  play(tracks: Track[], index: number) {
    this.queue = tracks;
    this.index = index;
    this.currentTime = 0;
    this.duration = 0;
    this.error = null;
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

  next() {
    if (!this.hasNext) return;
    this.index += 1;
    this.currentTime = 0;
    this.duration = 0;
    this.error = null;
    this.playing = true;
  }

  prev() {
    if (!this.hasPrev) return;
    this.index -= 1;
    this.currentTime = 0;
    this.duration = 0;
    this.error = null;
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
    if (this.duration > 0) this.currentTime = Math.min(this.duration, Math.max(0, ratio * this.duration));
  }

  fail(message: string) {
    this.playing = false;
    this.buffering = false;
    this.error = message;
  }
}

export const player = new Player();
