/** 播放器 store（SDD §5.4：全局唯一 <audio> 实例挂 PlayerBar）。 */

import { defineStore } from "pinia";

export interface Track {
  title: string;
  artist: string | null;
  streamUrl: string;
}

interface PlayerState {
  queue: Track[];
  index: number;
  playing: boolean;
  currentTime: number;
  duration: number;
}

export const usePlayerStore = defineStore("player", {
  state: (): PlayerState => ({
    queue: [],
    index: -1,
    playing: false,
    currentTime: 0,
    duration: 0,
  }),
  getters: {
    current: (state): Track | null => state.queue[state.index] ?? null,
    hasNext: (state): boolean => state.index < state.queue.length - 1,
    hasPrev: (state): boolean => state.index > 0,
  },
  actions: {
    /** 播放指定曲目；切歌先 pause 旧实例（SDD §5.4）。 */
    play(tracks: Track[], index: number) {
      this.queue = tracks;
      this.index = index;
      this.playing = true;
      this.currentTime = 0;
    },
    pause() {
      this.playing = false;
    },
    resume() {
      if (this.current) this.playing = true;
    },
    next() {
      if (this.hasNext) {
        this.index += 1;
        this.playing = true;
        this.currentTime = 0;
      }
    },
    prev() {
      if (this.hasPrev) {
        this.index -= 1;
        this.playing = true;
        this.currentTime = 0;
      }
    },
    stop() {
      this.playing = false;
      this.index = -1;
      this.currentTime = 0;
      this.duration = 0;
    },
    setTime(t: number) {
      this.currentTime = t;
    },
    setDuration(d: number) {
      this.duration = d;
    },
  },
});
