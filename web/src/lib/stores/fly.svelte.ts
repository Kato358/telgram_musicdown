/** 「飞进侧边栏下载」的动画请求（加入队列的即时反馈）。
 *
 * 行内「已加入队列」提示撤掉之后，成功的信号改为：一枚封面/音符从动作发生的位置
 * 飞向侧边栏「下载」导航图标，落定后图标弹一下（计数由 queue 快照自己更新）。
 * 这里是唯一的请求队列与落定信号源，`FlyOverlay` 是唯一的渲染方。
 */

export interface FlyOrigin {
  /** 起点（viewport 坐标，元素中心）。 */
  x: number;
  y: number;
}

export interface FlyItem extends FlyOrigin {
  id: number;
  /** 有封面就飞封面，没有就飞音符圆片。 */
  coverUrl: string | null;
  /** 批量时的级联延迟（ms）：逐枚错开，同一点起跳不显得机械。 */
  delay: number;
  /** 一批里的第一枚：落定时让导航图标弹一下（一批只弹一次）。 */
  bump: boolean;
}

/** 一批最多飞 6 枚：批量再大也是同一句「已加入」，多余的靠计数徽章说话。 */
const MAX_CHIPS = 6;
const STAGGER_MS = 70;

class FlyStore {
  items = $state<FlyItem[]>([]);
  /** 每有一批「落进」侧边栏就 +1：导航图标据此重放一次弹跳。 */
  pulse = $state(0);
  #seq = 0;

  /** 从 origin 起飞一批（单个下载传一项即可）。 */
  launch(origin: FlyOrigin, covers: (string | null)[]) {
    covers.slice(0, MAX_CHIPS).forEach((coverUrl, index) => {
      this.items = [
        ...this.items,
        {
          id: (this.#seq += 1),
          x: origin.x,
          y: origin.y,
          coverUrl,
          delay: index * STAGGER_MS,
          bump: index === 0,
        },
      ];
    });
  }

  /** 一枚飞完（FlyOverlay 调）：离场；带 bump 的那枚让导航图标弹一下。 */
  settle(id: number) {
    const item = this.items.find((candidate) => candidate.id === id);
    if (item === undefined) return;
    this.items = this.items.filter((candidate) => candidate.id !== id);
    if (item.bump) this.pulse += 1;
  }
}

export const fly = new FlyStore();
