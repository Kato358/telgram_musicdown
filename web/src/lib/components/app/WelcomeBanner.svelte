<script lang="ts">
  /** 欢迎横幅（设计规范 §5.6）：一屏之内的问候 + 内联搜索 + 推荐词，右侧是装饰。
   *
   * 横幅只做入口：它把关键词交给调用方（跳搜索页），自己不搜、不选源——
   * 源多选与筛选仍然只属于搜索页（§5.2）。
   * 装饰（光晕 + 音符方块 + 星点）一律 `aria-hidden`、`pointer-events-none`，不参与布局，
   * 也不承载信息；它们只在 ≥768px 出现，窄屏上横幅退化成一条干净的输入区。
   */
  import MusicIcon from "@lucide/svelte/icons/music";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SparklesIcon from "@lucide/svelte/icons/sparkles";
  import { Button } from "$lib/components/ui/button";

  interface Props {
    title: string;
    body: string;
    placeholder: string;
    submitLabel: string;
    tagsLabel: string;
    /** 推荐词：`label` 显示，`query` 提交。 */
    tags: { label: string; query: string }[];
    onsearch: (keyword: string) => void;
  }

  let { title, body, placeholder, submitLabel, tagsLabel, tags, onsearch }: Props = $props();

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
    onsearch(value);
  }
</script>

<section class="card surface-banner relative overflow-hidden p-5">
  <div
    class="banner-glow pointer-events-none absolute inset-y-0 right-0 hidden w-64 md:block"
    aria-hidden="true"
  ></div>

  <div
    class="pointer-events-none absolute top-1/2 right-8 hidden -translate-y-1/2 md:block"
    aria-hidden="true"
  >
    <span
      class="grid size-28 place-items-center rounded-card bg-card/70 text-primary ring-1 ring-primary/15"
    >
      <MusicIcon class="size-14" />
    </span>
    <SparklesIcon class="absolute -top-2 -left-3 size-5 text-primary/50" />
    <SparklesIcon class="absolute -right-2 -bottom-3 size-4 text-primary/40" />
  </div>

  <div class="relative flex flex-col gap-4 md:max-w-[60%]">
    <div class="flex flex-col gap-1">
      <h2 class="flex items-center gap-2 text-h2 font-semibold">
        <span
          class="grid size-7 shrink-0 place-items-center rounded-full bg-primary-soft text-primary"
          aria-hidden="true"
        >
          <SparklesIcon class="size-4" />
        </span>
        {title}
      </h2>
      <p class="max-w-[40ch] text-body text-muted-foreground">{body}</p>
    </div>

    <form class="flex flex-col gap-2 sm:flex-row" onsubmit={submit}>
      <input
        bind:this={field}
        bind:value={keyword}
        type="search"
        class="h-10 min-w-0 flex-1 rounded-full border border-border bg-card px-4 text-body outline-none placeholder:text-muted-foreground"
        {placeholder}
        aria-label={placeholder}
      />
      <Button size="lg" type="submit" class="shrink-0">
        <SearchIcon aria-hidden="true" />
        {submitLabel}
      </Button>
    </form>

    {#if tags.length > 0}
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-caption text-muted-foreground">{tagsLabel}</span>
        {#each tags as tag (tag.query)}
          <button
            type="button"
            class="ui-transition rounded-full border border-border bg-card px-2.5 py-1 text-caption text-muted-foreground hover:border-primary/30 hover:bg-primary-soft hover:text-primary"
            onclick={() => onsearch(tag.query)}
          >
            {tag.label}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</section>
