/** aplayer@1.10.1 不带类型声明：这里只声明本项目实际用到的那部分 API。 */

declare module "aplayer" {
  export interface APlayerAudio {
    name?: string;
    artist?: string;
    url?: string;
    cover?: string;
    theme?: string;
    type?: string;
    lrc?: string;
  }

  export interface APlayerList {
    /** 当前播放的列表下标。 */
    index: number;
    audios: APlayerAudio[];
    add(audios: APlayerAudio | APlayerAudio[]): void;
    remove(index: number): void;
    clear(): void;
    switch(index: number): void;
  }

  export interface APlayerOptions {
    container: HTMLElement;
    audio: APlayerAudio[];
    fixed?: boolean;
    mini?: boolean;
    autoplay?: boolean;
    theme?: string;
    loop?: "all" | "one" | "none";
    order?: "list" | "random";
    preload?: "auto" | "metadata" | "none";
    volume?: number;
    mutex?: boolean;
    lrcType?: number;
    listFolded?: boolean;
    listMaxHeight?: number | string;
    storageName?: string;
  }

  export default class APlayer {
    constructor(options: APlayerOptions);
    /** 原生 audio 元素（游离于 DOM 之外，需要时自行挂载）。 */
    audio: HTMLAudioElement;
    list: APlayerList;
    /** 音频事件（play/pause/ended/error/…）与播放器事件（listswitch/…）同名绑定；
     *  音频事件回调收到原生 Event，列表事件回调收到 `{ index }`。 */
    on(event: string, handler: (data: { index?: number }) => void): void;
    play(): void;
    pause(): void;
    seek(time: number): void;
    notice(text: string, time?: number, opacity?: number): void;
    theme(color: string, index?: number): void;
    destroy(): void;
  }
}
