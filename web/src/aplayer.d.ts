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
    /** 原生 audio 元素（游离于 DOM 之外，需要时自行挂载）。已挂载的宿主元素。 */
    audio: HTMLAudioElement;
    container: HTMLElement;
    list: APlayerList;
    /** 当前形态：`mini` = 收起（只剩封面片），`normal` = 展开（机身 + 列表）。
     *  它是 APlayer 自己的状态，改它只能走 `setMode`（折叠把手按钮也走这条路）。 */
    readonly mode: "mini" | "normal";
    /** 收起 / 展开机身：APlayer 折叠把手按钮的同一个入口，尺寸与把手图标由它自己维护。 */
    setMode(mode: "mini" | "normal"): void;
    /** 归一化后的选项。`loop` / `order` 是播放模式的唯一所有者：APlayer 自己的
     *  模式按钮就地改它们，我们只读、不改（见 player store 的 cycleMode）。 */
    options: APlayerOptions;
    /** 音频时长（秒）；未就绪时 0（它把 NaN 收敛掉了）。 */
    readonly duration: number;
    /** 音频事件（play/pause/timeupdate/…）与播放器事件（listswitch/…）同名绑定；
     *  音频事件回调收到原生 Event，列表事件回调收到 `{ index }`。 */
    on(event: string, handler: (data: { index?: number } | Event) => void): void;
    play(): void;
    pause(): void;
    /** 跳到指定秒数（内部按 duration 夹取）。 */
    seek(time: number): void;
    /** 设音量 0..1；同时更新 APlayer 自己的音量条与图标。 */
    volume(volume: number, index?: number): void;
    /** 上一首 / 下一首：内部走 prevIndex/nextIndex，随机顺序下也对。 */
    skipBack(): void;
    skipForward(): void;
    notice(text: string, time?: number, opacity?: number): void;
    theme(color: string, index?: number): void;
    destroy(): void;
  }
}
