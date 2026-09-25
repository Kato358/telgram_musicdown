<script lang="ts">
  /** 「飞进侧边栏」动画的全局渲染层：挂在 App 外壳（TransportBar 旁），路由切换不经过这里。
   *
   * 每枚圆片用 WAAPI 从起点（下载按钮/批量按钮中心）飞向侧边栏「下载」导航图标：
   * 桌面飞侧边栏；<768px 侧栏收起，退而飞汉堡键（打开抽屉就能看到计数徽章）。
   * prefers-reduced-motion 或找不到可见目标时不飞，直接结算（徽章计数照常更新）。
   */
  import MusicIcon from "@lucide/svelte/icons/music";
  import { fly, type FlyItem } from "$lib/stores/fly.svelte";

  const FLIGHT_MS = 620;

  /** 飞行目标：桌面侧边栏的下载图标优先，不可见（移动端）退到汉堡键。 */
  function targetRect(): DOMRect | null {
    for (const selector of ["[data-fly-downloads]", "[data-fly-navmenu]"]) {
      const rect = document.querySelector<HTMLElement>(selector)?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) return rect;
    }
    return null;
  }

  /** 圆片的飞行：起点由 left/top 定在元素中心，transform 里补 -50% 居中后飞向目标。 */
  function flight(node: HTMLDivElement, item: FlyItem): { destroy(): void } {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rect = targetRect();
    if (reduced || rect === null) {
      const timer = window.setTimeout(() => fly.settle(item.id), 60);
      return { destroy: () => window.clearTimeout(timer) };
    }
    const dx = rect.left + rect.width / 2 - item.x;
    const dy = rect.top + rect.height / 2 - item.y;
    const animation = node.animate(
      [
        { transform: "translate(-50%, -50%) scale(1)", opacity: 1 },
        {
          transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(0.32)`,
          opacity: 0.35,
        },
      ],
      {
        duration: FLIGHT_MS,
        delay: item.delay,
        easing: "cubic-bezier(0.5, 0.05, 0.7, 0.4)",
        fill: "both",
      },
    );
    animation.onfinish = () => fly.settle(item.id);
    return { destroy: () => animation.cancel() };
  }
</script>

{#each fly.items as item (item.id)}
  <div
    class="fly-chip grid place-items-center overflow-hidden bg-primary-soft text-primary"
    style="left: {item.x}px; top: {item.y}px"
    use:flight={item}
    aria-hidden="true"
  >
    {#if item.coverUrl}
      <img src={item.coverUrl} alt="" class="size-8 object-cover" />
    {:else}
      <MusicIcon class="size-4" />
    {/if}
  </div>
{/each}
