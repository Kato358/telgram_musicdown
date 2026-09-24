<script lang="ts">
  /** 页面横幅（设计规范 §5.6）：一屏的开场 + 可选的内联搜索 + 可选的卡内内容区。
   *
   * 仪表盘用它当「欢迎横幅」（带内联搜索与推荐词），下载页用它当「绿色容器」——
   * 标题区之下由 `children` 接住四张统计卡（参考图的做法：卡片坐在横幅的淡绿上，
   * 不再另起一层页面背景）。搜索块与 `children` 都是**可选**的：没给就不渲染，
   * 不为没有入口的页面画一个输入框。
   * 横幅只做入口：搜索关键词交给调用方（跳搜索页），自己不搜、不选源——
   * 源多选与筛选仍然只属于搜索页（§5.2）。
   * 装饰（光晕 + 音符圆徽 + 波浪）一律 `aria-hidden`、`pointer-events-none`，不参与布局，
   * 也不承载信息；它们只在 ≥768px 出现，窄屏上横幅退化成一条干净的标题区。
   */
  import MusicIcon from "@lucide/svelte/icons/music";
  import SearchIcon from "@lucide/svelte/icons/search";
  import type { Snippet } from "svelte";
  import { Button } from "$lib/components/ui/button";

  /** 内联搜索块：给了才渲染（`onsearch` 里交给调用方跳页）。 */
  interface BannerSearch {
    placeholder: string;
    submitLabel: string;
    tagsLabel: string;
    /** 推荐词：`label` 显示，`query` 提交。 */
    tags: { label: string; query: string }[];
    onsearch: (keyword: string) => void;
  }

  interface Props {
    title: string;
    body: string;
    search?: BannerSearch;
    /** 标题区之下的卡内内容（下载页放统计卡栅格）。 */
    children?: Snippet;
  }

  let { title, body, search, children }: Props = $props();

  let keyword = $state("");
  let field = $state<HTMLInputElement | undefined>();

  /** 空关键词不搜，也不把主按钮做成灰的（横幅上的主按钮变灰比「点了没反应」更难看）——
   *  把焦点交给那个还空着的输入框，用户知道下一步该做什么。 */
  function submit(event: SubmitEvent) {
    event.preventDefault();
    const value = keyword.trim();
    if (value.length === 0) {
      field?.focus();
      return;
    }
    search?.onsearch(value);
  }
</script>

<section class="card surface-banner relative overflow-hidden p-5">
  <div
    class="banner-glow pointer-events-none absolute inset-y-0 right-0 hidden w-64 md:block"
    aria-hidden="true"
  ></div>

  <!-- 波浪：贴着横幅右下缘的两层淡绿，画在内容之下——后面的 relative 内容会盖住它 -->
  <div
    class="pointer-events-none absolute inset-y-0 right-0 hidden w-80 md:block"
    aria-hidden="true"
  >
    <svg
      class="absolute right-0 bottom-0 h-32 w-80 text-primary/15"
      viewBox="0 0 320 128"
      fill="currentColor"
      preserveAspectRatio="none"
    >
      <path d="M0 128 C 84 26 178 116 320 8 L 320 128 Z"></path>
    </svg>
    <svg
      class="absolute right-0 bottom-0 h-24 w-64 text-primary/10"
      viewBox="0 0 256 96"
      fill="currentColor"
      preserveAspectRatio="none"
    >
      <path d="M0 96 C 72 16 152 82 256 4 L 256 96 Z"></path>
    </svg>
  </div>

  <div class="relative flex items-center gap-4">
    <div class="flex min-w-0 flex-1 flex-col gap-1 md:max-w-[60%]">
      <h2 class="text-h2 font-semibold">{title}</h2>
      <p class="max-w-[40ch] text-body text-muted-foreground">{body}</p>

      {#if search}
        <form class="mt-3 flex flex-col gap-2 sm:flex-row" onsubmit={submit}>
          <input
            bind:this={field}
            bind:value={keyword}
            type="search"
            class="h-10 min-w-0 flex-1 rounded-full border border-border bg-card px-4 text-body outline-none placeholder:text-muted-foreground"
            placeholder={search.placeholder}
            aria-label={search.placeholder}
          />
          <Button size="lg" type="submit" class="shrink-0">
            <SearchIcon aria-hidden="true" />
            {search.submitLabel}
          </Button>
        </form>

        {#if search.tags.length > 0}
          <div class="mt-1 flex flex-wrap items-center gap-2">
            <span class="text-caption text-muted-foreground">{search.tagsLabel}</span>
            {#each search.tags as tag (tag.query)}
              <button
                type="button"
                class="ui-transition rounded-full border border-border bg-card px-2.5 py-1 text-caption text-muted-foreground hover:border-primary/30 hover:bg-primary-soft hover:text-primary"
                onclick={() => search.onsearch(tag.query)}
              >
                {tag.label}
              </button>
            {/each}
          </div>
        {/if}
      {/if}
    </div>

    <!-- 音符圆徽：与标题区垂直居中，白底从渐变里浮出来 -->
    <div
      class="pointer-events-none absolute top-1/2 right-1 hidden -translate-y-1/2 md:block"
      aria-hidden="true"
    >
      <span
        class="grid size-16 place-items-center rounded-full bg-card/80 text-primary ring-1 ring-primary/15"
      >
        <MusicIcon class="size-7" />
      </span>
    </div>
  </div>

  {#if children}
    <div class="relative mt-4">
      {@render children()}
    </div>
  {/if}
</section>
