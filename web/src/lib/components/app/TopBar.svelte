<script lang="ts">
  /** 顶栏与全局搜索（设计规范 §5.2）：页面标题 + 搜索（Ctrl/⌘ K）+ 在传读数 + 外观/通知/连接/头像。
   *
   * 顶栏只做入口：不选源、不筛选，那些属于搜索页。连接状态并入头像区（8px 灯 + 状态词）。
   * 底为毛玻璃（`.topbar-glass`，v3.21）：主栏内容从顶栏下方滚过，透出模糊后的底色；
   * 不支持 backdrop-filter 或系统要求降低透明度时回落实色 --card（回落逻辑在 app.css）。
   */
  import { tick } from "svelte";
  import BellIcon from "@lucide/svelte/icons/bell";
  import CheckIcon from "@lucide/svelte/icons/check";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SunMoonIcon from "@lucide/svelte/icons/sun-moon";
  import UserIcon from "@lucide/svelte/icons/user";
  import XIcon from "@lucide/svelte/icons/x";
  import { Button } from "$lib/components/ui/button";
  import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
  } from "$lib/components/ui/dropdown-menu";
  import { t } from "$lib/i18n/index.svelte";
  import { formatRate } from "$lib/format";
  import { navigate, pathOf } from "$lib/router.svelte";
  import { events } from "$lib/stores/events.svelte";
  import { queue } from "$lib/stores/queue.svelte";
  import { session } from "$lib/stores/session.svelte";
  import { theme, THEME_OPTIONS } from "$lib/stores/theme.svelte";
  import Lamp from "./Lamp.svelte";
  import MobileNav from "./MobileNav.svelte";

  interface Props {
    /** App 里单点注册的 Ctrl/⌘+K 递增它，顶栏据此聚焦搜索框。 */
    focusSignal?: number;
  }

  let { focusSignal = 0 }: Props = $props();

  let query = $state("");
  /** <768px：搜索收成图标按钮，点开占满顶栏的浮层。 */
  let mobileOpen = $state(false);
  let desktopInput = $state<HTMLInputElement | undefined>();
  let mobileInput = $state<HTMLInputElement | undefined>();
  let searchButton = $state<HTMLButtonElement | null>(null);

  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);
  const shortcut = isMac ? "⌘ K" : "Ctrl K";

  /** 总速率 = 下载中任务的进度帧速率之和（SSE 单点订阅，不自开连接）。 */
  const rate = $derived(
    queue.downloading.reduce((sum, task) => sum + (events.progress[task.id]?.speed ?? 0), 0),
  );
  const errorCount = $derived(events.errors.length);
  const connection = $derived(
    session.me === null
      ? { tone: "idle" as const, label: t("app.connecting") }
      : session.connected
        ? { tone: "done" as const, label: t("app.connected") }
        : { tone: "idle" as const, label: t("app.disconnected") },
  );
  const initial = $derived(session.handle?.trim().charAt(0).toUpperCase() ?? "");

  $effect(() => {
    if (focusSignal === 0) return;
    void focusSearch();
  });

  async function focusSearch() {
    if (window.matchMedia("(min-width: 768px)").matches) {
      await tick();
      desktopInput?.focus();
      desktopInput?.select();
    } else {
      mobileOpen = true;
      await tick();
      mobileInput?.focus();
    }
  }

  function submit() {
    const keyword = query.trim();
    if (keyword.length === 0) return;
    navigate(`${pathOf("search")}?q=${encodeURIComponent(keyword)}`);
    mobileOpen = false;
  }

  /** 关闭移动端浮层并把焦点还给触发键（§7）。 */
  async function closeMobile() {
    mobileOpen = false;
    await tick();
    searchButton?.focus();
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    } else if (event.key === "Escape") {
      // Esc 清空并失焦（§5.2）
      query = "";
      if (mobileOpen) void closeMobile();
      else (event.currentTarget as HTMLInputElement).blur();
    }
  }
</script>

<header
  class="topbar-glass sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 border-b border-border px-4 md:px-6"
>
  {#if mobileOpen}
    <div class="flex w-full items-center gap-2 md:hidden">
      <div
        class="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-full border border-border bg-surface-subtle px-3.5"
      >
        <SearchIcon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <input
          bind:this={mobileInput}
          bind:value={query}
          type="search"
          class="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-muted-foreground"
          placeholder={t("topbar.searchPlaceholder")}
          aria-label={t("topbar.searchLabel")}
          onkeydown={onKeydown}
        />
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("common.cancel")}
        onclick={() => void closeMobile()}
      >
        <XIcon />
      </Button>
    </div>
  {:else}
    <MobileNav />

    <div class="hidden min-w-0 flex-1 md:flex md:max-w-[480px]">
      <div
        class="flex h-10 min-w-0 w-full items-center gap-2 rounded-full border border-border bg-surface-subtle px-3.5"
      >
        <SearchIcon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <input
          bind:this={desktopInput}
          bind:value={query}
          type="search"
          class="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-muted-foreground"
          placeholder={t("topbar.searchPlaceholder")}
          aria-label={t("topbar.searchLabel")}
          onkeydown={onKeydown}
        />
        <kbd
          class="tabular hidden shrink-0 rounded-chip bg-rule px-1.5 py-0.5 text-caption text-muted-foreground lg:block"
        >
          {shortcut}
        </kbd>
      </div>
    </div>

    <div class="ml-auto flex shrink-0 items-center gap-1">
      {#if queue.activeCount > 0}
        <button
          type="button"
          class="ui-transition mr-1 hidden items-center gap-2 rounded-control px-2 py-1.5 hover:bg-rule sm:flex"
          onclick={() => navigate(pathOf("downloads"))}
        >
          <span class="tabular text-caption text-foreground">
            {t("app.activeTransfers")}
            {queue.activeCount}
          </span>
          <span class="tabular text-caption text-faint-foreground">{formatRate(rate)}</span>
        </button>
      {/if}

      <Button
        bind:ref={searchButton}
        variant="ghost"
        size="icon"
        class="md:hidden"
        aria-label={t("topbar.searchLabel")}
        onclick={() => void focusSearch()}
      >
        <SearchIcon />
      </Button>

      <DropdownMenu>
        <DropdownMenuTrigger>
          {#snippet child({ props })}
            <Button {...props} variant="ghost" size="icon" aria-label={t("app.theme")}>
              <SunMoonIcon />
            </Button>
          {/snippet}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" class="min-w-36">
          {#each THEME_OPTIONS as option (option.value)}
            <DropdownMenuItem onSelect={() => theme.set(option.value)} class="justify-between">
              {t(option.label)}
              {#if theme.preference === option.value}
                <CheckIcon class="size-4" />
              {/if}
            </DropdownMenuItem>
          {/each}
        </DropdownMenuContent>
      </DropdownMenu>

      <Button
        variant="ghost"
        size="icon"
        class="relative"
        aria-label={t("topbar.notifications")}
        onclick={() => navigate(pathOf("logs"))}
      >
        <BellIcon />
        {#if errorCount > 0}
          <span
            class="absolute top-1.5 right-1.5 size-2 rounded-full bg-destructive"
            aria-hidden="true"
          ></span>
        {/if}
      </Button>

      <Lamp tone={connection.tone} label={connection.label} class="ml-1 hidden md:inline-flex" />

      <span
        class="ml-1 grid size-8 shrink-0 place-items-center rounded-full bg-primary-soft text-caption font-medium text-primary"
        title={session.handle ?? t("topbar.account")}
      >
        {#if initial}
          {initial}
        {:else}
          <UserIcon class="size-4" aria-hidden="true" />
        {/if}
      </span>
    </div>
  {/if}
</header>
