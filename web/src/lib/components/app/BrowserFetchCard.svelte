<script lang="ts">
  /** 浏览器下载的「取回中」任务卡（FR-DL-08）：右下角，**非模态**。
   *
   *  它替掉的是原来那一行行内小字（v3.27「准备中：正在取回这首，稍后浏览器会自动开始保存。」）：
   *  这段等待可能是分钟级（大文件 + 慢源，前端超时给到 10 分钟），12px 的行内文案随表格滚走，
   *  用户只记得「点了没反应」。
   *
   *  三条：
   *  1. **非模态**——等待期没有需要用户确认的事，挡住界面只会让他没法继续挑歌、试听、切页；
   *  2. **真进度**——服务端边取边推字节（`store.browserDownload` 消费那条 SSE），有总量就画
   *     百分比；总量报不出来（部分在线源）退回不确定态的游标呼吸，不假装 0%（§5.5）；
   *  3. **可取消**——改主意不必再等那几分钟：取消即断开那条流，服务端随之停掉取数。
   *     真正的下载进度在卡片消失、浏览器接管下载之后，由浏览器原生下载器给。
   */
  import { t } from "$lib/i18n/index.svelte";
  import { search, type BrowserJob } from "$lib/stores/search.svelte";
  import ProgressBar from "./ProgressBar.svelte";

  /** 每首一张卡：服务端取数是串行的（并发槽 1），但前端可以连点好几行——
   *  一次请求一张卡，比合成一个「队列」更直白（哪首在飞、飞了多久一眼看到）。
   *  `key` 是曲目定位键，正好当 each 的 key（同曲不会重复在飞）。
   *  **最新点的排最上**：容器底部锚定、向上生长，溢出时可滚动的窗口首先看到的就是
   *  刚点的那一首（按插入序的话，最新那张会被挤到滚动区外面）。 */
  const jobs = $derived(
    Object.entries(search.browserPending)
      .map(([key, job]) => ({ key, ...job }))
      .reverse(),
  );

  /** 秒表：一秒一跳。定时器只在真有作业时挂，读数靠它驱动重算。 */
  let now = $state(Date.now());
  $effect(() => {
    if (jobs.length === 0) return;
    now = Date.now();
    const timer = window.setInterval(() => (now = Date.now()), 1000);
    return () => window.clearInterval(timer);
  });

  /** 百分比：总量未知或为 0 时给 null（不确定态），否则 0..1。向下取整——定时器有毫秒级
   *  漂移，`round` 会让读数偶尔跳一格（同一秒里显示两个数）。 */
  function ratioOf(job: BrowserJob): number | null {
    if (job.total === null || job.total <= 0) return null;
    return Math.min(1, Math.max(0, job.loaded / job.total));
  }

  /** 已等待时长：一分以内只给秒，超过就切分秒——等到 547 秒时没人愿意自己换算。 */
  function elapsedText(startedAt: number): string {
    const total = Math.max(0, Math.floor((now - startedAt) / 1000));
    if (total < 60) return t("search.browserFetchElapsedSec", { s: total });
    return t("search.browserFetchElapsedMin", { m: Math.floor(total / 60), s: total % 60 });
  }

  /** 状态行：与进度条同一套判断（有总量说百分比，没有就只说在取）。 */
  function statusText(job: BrowserJob): string {
    const ratio = ratioOf(job);
    const elapsed = elapsedText(job.startedAt);
    if (ratio === null) return t("search.browserFetchStatus", { elapsed });
    return t("search.browserFetchStatusPercent", {
      percent: Math.floor(ratio * 100),
      elapsed,
    });
  }
</script>

{#if jobs.length > 0}
  <!-- 层次：只压在页面内容之上（z-20），**刻意低于**吸底播放器（z-30）、移动端抽屉遮罩（z-40）
       与对话框（z-50）。展开播放器歌单（最高 420px）时让位给歌单，不遮它的行与点击——
       提示晚一点看到没关系，歌单点不动才是问题。
       max-h 兜住连点：服务端取数串行，但前端可以连点好几行，卡片会向上叠；不设上限时
       8 张卡就能长出视口顶端（overflow 也不给的话后面的卡永远够不到）。 -->
  <div
    class="fixed right-4 bottom-24 z-20 flex max-h-[calc(100dvh-8rem)] w-[min(320px,calc(100vw-32px))] flex-col gap-2 overflow-y-auto"
    role="status"
  >
    {#each jobs as job (job.key)}
      <!-- 浮层海拔用 e3（`shadow-md` 即 `--shadow-e3`），与吸底播放器同一档——
           它压在页面内容之上，用卡片档 e2 会「比被压住的东西还浅」。 -->
      <div class="flex flex-col gap-1.5 rounded-card border border-border bg-card p-3 shadow-md">
        <!-- 读屏只念这一句 + 曲名 + 取消：role="status" 隐含 aria-atomic，整块子树都会被念，
             而百分比与秒表每秒都在变——它们一律 aria-hidden，否则播报会变成刷屏。
             取消按钮**不能**藏：藏了读屏用户就没法取消。 -->
        <span class="sr-only">{t("search.browserFetchTitle")}</span>
        <div class="flex items-center gap-2">
          <p class="min-w-0 flex-1 truncate text-body font-medium" title={job.title}>
            {job.title}
          </p>
          <button
            type="button"
            class="ui-transition shrink-0 rounded-chip px-2 py-0.5 text-caption text-muted-foreground hover:bg-rule hover:text-foreground"
            onclick={() => search.cancelBrowserDownload(job.key)}
          >
            {t("search.browserFetchCancel")}
          </button>
        </div>
        <div aria-hidden="true" class="flex flex-col gap-1.5">
          <ProgressBar ratio={ratioOf(job)} label={t("search.browserFetchTitle")} />
          <p class="tabular text-caption text-muted-foreground">{statusText(job)}</p>
          <p class="text-caption text-faint-foreground">{t("search.browserFetchHint")}</p>
        </div>
      </div>
    {/each}
  </div>
{/if}
