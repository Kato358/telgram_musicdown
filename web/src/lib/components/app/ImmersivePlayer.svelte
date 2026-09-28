<script lang="ts">
  /** 沉浸全屏（设计规范 §5.4）：把正在播的那首铺满一屏。
   *
   *  模糊封面做底、唱片做前景，右侧整篇可点的歌词，右下角歌单从右边挤开内容而不盖住它。
   *  播放本身在 store 里（全站唯一一个 <audio>），这一层只读状态、发指令——开与关都不碰
   *  播放，所以左上角「返回」只关掉这一层，歌照响。
   *
   *  歌词是 store 按当前曲目取的那一份（有时间轴，可点跳转）；APlayer 自己那条单行歌词条
   *  没有时间轴，画不了这一层要的东西，因此两者各取所需、互不干扰。
   */
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import ListMusicIcon from "@lucide/svelte/icons/list-music";
  import MessageSquareQuoteIcon from "@lucide/svelte/icons/message-square-quote";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import Repeat1Icon from "@lucide/svelte/icons/repeat-1";
  import RepeatIcon from "@lucide/svelte/icons/repeat";
  import ShuffleIcon from "@lucide/svelte/icons/shuffle";
  import SkipBackIcon from "@lucide/svelte/icons/skip-back";
  import SkipForwardIcon from "@lucide/svelte/icons/skip-forward";
  import Volume2Icon from "@lucide/svelte/icons/volume-2";
  import XIcon from "@lucide/svelte/icons/x";
  import { formatDuration } from "$lib/format";
  import { t } from "$lib/i18n/index.svelte";
  import { player } from "$lib/stores/player.svelte";

  /** 歌单抽屉开合：这一层的局部状态，关掉沉浸即复位。 */
  /** 歌单抽屉开合：这一层的局部状态，关掉沉浸即复位。 */
  let listOpen = $state(false);
  /** 歌词页（仅移动端）：唱片区整片换成歌词，再按一次换回唱片。桌面端歌词常驻，不需要这档。 */
  let showLyrics = $state(false);
  /** 音量滑条展开（仅移动端）：窄屏音量收成一枚按钮，点开在按钮正上方弹一条纵向滑条。 */
  let volOpen = $state(false);
  /** 退场动画进行中：这一层要多挂到退场动画结束才卸载（见 .is-closing 的动画）。 */
  let closing = $state(false);
  let closeTimer = 0;
  /** 进度条拖动中：本地拿着当前值，免得 4Hz 的 timeupdate 把拇指拽回去。 */
  let scrubbing = $state(false);
  let scrubValue = $state(0);
  let lyricsBox = $state<HTMLDivElement>();

  const track = $derived(player.current);
  const shownTime = $derived(scrubbing ? scrubValue : player.time);
  /** 进度条已播段的百分比：交给 CSS 的 --p 画渐变填充（range 的轨道没法按值着色）。 */
  const progressPct = $derived(
    player.duration > 0 ? Math.min(100, Math.max(0, (shownTime / player.duration) * 100)) : 0,
  );
  /** 模式按钮的文案键：档位与图标一一对应。 */
  const modeKey = $derived(
    {
      list: "player.immersive.modeList",
      one: "player.immersive.modeOne",
      random: "player.immersive.modeRandom",
    }[player.mode],
  );

  /** 当前唱到第几行：最后一条 time ≤ 当前位置的歌词。 */
  const activeLine = $derived.by(() => {
    const lines = player.lyrics;
    let n = -1;
    for (let i = 0; i < lines.length; i += 1) if (player.time >= lines[i].time) n = i;
    return n;
  });

  function close() {
    if (closing) return;
    listOpen = false;
    showLyrics = false;
    volOpen = false;
    scrubbing = false;
    // 减少动效时没有退场动画：立刻卸载，不留一段 320ms 的死等
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      player.closeImmersive();
      return;
    }
    // 先关掉业务态（player.immersive = false），再挂着这一层走退场动画；
    // 若顺序反过来，退场守卫（见下）看到 immersive 还是 true，会当作「重进」把卸载撤掉。
    closing = true;
    player.closeImmersive();
    closeTimer = window.setTimeout(() => {
      closing = false;
    }, 320);
  }

  // 退场窗口里又开了沉浸（入口键透过渐隐的层被点到）：撤掉待定的卸载
  $effect(() => {
    if (player.immersive && closing) {
      window.clearTimeout(closeTimer);
      closing = false;
    }
  });

  // 音量滑条（仅移动端）点到别处就收回去。按钮与滑条自己那一块不关：拖滑条时 pointerdown
  // 就落在里面（closest 命中），否则一按滑条它自己先没了。
  $effect(() => {
    if (!volOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const el = event.target;
      if (el instanceof Element && el.closest(".immersive-volume")) return;
      volOpen = false;
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  });

  // 当前行滚到歌词区正中（行高不一，按实际几何算，不按行号乘行高）
  $effect(() => {
    const n = activeLine;
    const box = lyricsBox;
    if (!player.immersive || n < 0 || !box) return;
    const line = box.children[n] as HTMLElement | undefined;
    if (!line) return;
    box.scrollTo({
      top: line.offsetTop - box.clientHeight / 2 + line.clientHeight / 2,
      behavior: "smooth",
    });
  });

  // 沉浸期间接管 Esc（返回）与空格（播放/暂停）；焦点在输入件里时不抢空格
  $effect(() => {
    if (!player.immersive) return;
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // 音量滑条开着时 Esc 先收滑条，再按一次才离开沉浸
        if (volOpen) {
          volOpen = false;
          return;
        }
        close();
        return;
      }
      if (event.code !== "Space") return;
      const active = document.activeElement;
      const typing =
        active instanceof HTMLElement &&
        (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable);
      if (typing) return;
      event.preventDefault();
      player.toggle();
    };
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  });
</script>

{#if (player.immersive || closing) && track}
  <section
    class="immersive"
    class:is-playing={player.playing}
    class:is-list-open={listOpen}
    class:is-closing={closing}
    class:is-lyrics={showLyrics}
    class:is-vol-open={volOpen}
    aria-label={t("player.immersive.enter")}
  >
    <div class="immersive-backdrop" aria-hidden="true">
      {#if track.cover}<img src={track.cover} alt="" />{/if}
    </div>
    <div class="immersive-veil" aria-hidden="true"></div>

    <div class="immersive-layout">
      <div class="immersive-left">
        <header class="immersive-topbar">
          <button
            class="immersive-icon-btn"
            onclick={close}
            title={t("player.immersive.back")}
            aria-label={t("player.immersive.back")}
          >
            <ChevronLeftIcon size={18} />
          </button>
          <!-- 歌单入口：窄屏用这一枚（桌面那枚把手骑在抽屉边上；窄屏抽屉改从底部升起，
               把手留在侧边就成了两个对不上的控件）。`margin-left: auto` 把它和歌词键
               一起顶到右侧。 -->
          <button
            class="immersive-icon-btn immersive-list-btn"
            class:is-open={listOpen}
            onclick={() => (listOpen = !listOpen)}
            title={t(listOpen ? "player.immersive.closeList" : "player.immersive.openList")}
            aria-label={t(listOpen ? "player.immersive.closeList" : "player.immersive.openList")}
            aria-expanded={listOpen}
          >
            <ListMusicIcon size={18} />
          </button>
          <button
            class="immersive-icon-btn immersive-lyrics-toggle"
            class:is-active={showLyrics}
            onclick={() => (showLyrics = !showLyrics)}
            title={t("player.immersive.lyrics")}
            aria-label={t("player.immersive.lyrics")}
            aria-pressed={showLyrics}
          >
            <MessageSquareQuoteIcon size={18} />
          </button>
        </header>

        <div class="immersive-cover">
          <div class="immersive-vinyl-wrap">
            <div class="immersive-vinyl">
              {#if track.cover}<img src={track.cover} alt="" />{/if}
              <span class="immersive-spindle"></span>
            </div>
          </div>

          <div class="immersive-song">
            <h2 title={track.title}>{track.title}</h2>
            <div class="immersive-artist">{track.artist ?? t("common.placeholder")}</div>
          </div>
        </div>

        <div class="immersive-deck">
          <div class="immersive-times">
            <span>{formatDuration(shownTime)}</span>
            <span>{formatDuration(player.duration)}</span>
          </div>
          <input
            class="immersive-seek"
            type="range"
            min="0"
            max={player.duration > 0 ? player.duration : 0}
            step="0.1"
            value={shownTime}
            style="--p: {progressPct}%"
            aria-label={t("player.immersive.seek")}
            aria-valuetext={formatDuration(shownTime)}
            oninput={(event) => {
              scrubValue = Number(event.currentTarget.value);
              scrubbing = true;
              player.seek(scrubValue);
            }}
            onchange={() => (scrubbing = false)}
          />

          <div class="immersive-controls">
            <button
              class="immersive-btn"
              onclick={() => player.cycleMode()}
              title={t(modeKey)}
              aria-label={t(modeKey)}
            >
              {#if player.mode === "random"}<ShuffleIcon size={18} />
              {:else if player.mode === "one"}<Repeat1Icon size={18} />
              {:else}<RepeatIcon size={18} />{/if}
            </button>
            <button
              class="immersive-btn"
              onclick={() => player.prev()}
              aria-label={t("player.immersive.prev")}
            >
              <SkipBackIcon size={20} />
            </button>
            <button
              class="immersive-play"
              onclick={() => player.toggle()}
              aria-label={t(player.playing ? "player.immersive.pause" : "player.immersive.play")}
            >
              {#if player.playing}<PauseIcon size={26} />
              {:else}<PlayIcon size={26} />{/if}
            </button>
            <button
              class="immersive-btn"
              onclick={() => player.next()}
              aria-label={t("player.immersive.next")}
            >
              <SkipForwardIcon size={20} />
            </button>
            <!-- 音量：桌面是「图标 + 横条」一行；窄屏收成一枚按钮，点开在它正上方弹一条
                 纵向滑条（同一个 <input>，由 CSS 转 90° 摆过去）。 -->
            <div class="immersive-volume">
              <span class="immersive-volume-icon" aria-hidden="true"><Volume2Icon size={18} /></span
              >
              <button
                class="immersive-volume-btn"
                onclick={() => (volOpen = !volOpen)}
                aria-expanded={volOpen}
                aria-label={t("player.immersive.volume")}
              >
                <Volume2Icon size={18} />
              </button>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={player.volume}
                style="--v: {player.volume * 100}%"
                aria-label={t("player.immersive.volume")}
                oninput={(event) => player.setVolume(Number(event.currentTarget.value))}
              />
            </div>
          </div>
        </div>
      </div>

      <div class="immersive-right">
        <div class="immersive-lyrics" bind:this={lyricsBox}>
          {#if player.lyrics.length === 0}
            <p class="immersive-lyric-empty">{t("player.immersive.lyricsEmpty")}</p>
          {:else}
            {#each player.lyrics as line, i (i)}
              <button
                class="immersive-line"
                class:is-active={i === activeLine}
                onclick={() => player.seek(line.time)}
              >
                <span class="immersive-line-text">{line.text}</span>
              </button>
            {/each}
          {/if}
        </div>
      </div>

      <aside class="immersive-drawer" aria-hidden={!listOpen} inert={!listOpen}>
        <div class="immersive-drawer-inner">
          <!-- 窄屏底部浮层的手柄：戳一下收起（桌面抽屉从右侧进来，不需要这一段） -->
          <button
            class="immersive-drawer-grab"
            onclick={() => (listOpen = false)}
            aria-label={t("player.immersive.closeList")}
          ></button>
          <header class="immersive-drawer-head">
            <h2>{t("player.immersive.list")}</h2>
            <span class="immersive-drawer-count">
              {t("player.immersive.trackCount", { n: player.queue.length })}
            </span>
            <button
              class="immersive-drawer-close"
              onclick={() => (listOpen = false)}
              aria-label={t("player.immersive.closeList")}
            >
              <XIcon size={17} />
            </button>
          </header>

          {#if player.queue.length === 0}
            <p class="immersive-queue-empty">{t("player.immersive.queueEmpty")}</p>
          {:else}
            <ol class="immersive-queue">
              {#each player.queue as item, i (item.id)}
                <li>
                  <button
                    class="immersive-row"
                    class:is-current={i === player.index}
                    aria-current={i === player.index ? "true" : undefined}
                    onclick={() => player.playAt(i)}
                  >
                    <span class="immersive-row-index">
                      {#if i === player.index}
                        <span class="immersive-eq" aria-hidden="true">
                          <i></i><i></i><i></i>
                        </span>
                      {:else}
                        {i + 1}
                      {/if}
                    </span>
                    <span class="immersive-row-cover">
                      {#if item.cover}<img src={item.cover} alt="" />{/if}
                    </span>
                    <span class="immersive-row-meta">
                      <span class="immersive-row-title">{item.title}</span>
                      <span class="immersive-row-artist">
                        {item.artist ?? t("common.placeholder")}
                      </span>
                    </span>
                  </button>
                </li>
              {/each}
            </ol>
          {/if}
        </div>
      </aside>

      <!-- 歌单把手：钉在窗口最右侧、垂直居中。收起态朝左（把面板拉进来），展开态朝右（推回去）。
           放在抽屉之后，DOM 顺序与 z-index 都保证它压在这一列之上。 -->
      <button
        class="immersive-list-toggle"
        class:is-open={listOpen}
        onclick={() => (listOpen = !listOpen)}
        title={t(listOpen ? "player.immersive.closeList" : "player.immersive.openList")}
        aria-label={t(listOpen ? "player.immersive.closeList" : "player.immersive.openList")}
        aria-expanded={listOpen}
      >
        {#if listOpen}<ChevronRightIcon size={18} />{:else}<ChevronLeftIcon size={18} />{/if}
      </button>
    </div>
  </section>
{/if}
