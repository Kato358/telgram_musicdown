<script lang="ts">
  /** 应用内链接：拦截左键跳转交给 router，其余（新窗口、复制链接）保持浏览器原生行为。 */
  import type { Snippet } from "svelte";
  import { navigate } from "$lib/router.svelte";

  interface Props {
    href: string;
    /** 该链接指向当前路由时高亮（由调用方给出，避免 Link 自己猜）。 */
    active?: boolean;
    /** 图标模式下用 title 补全文字（§5.1）。 */
    title?: string;
    class?: string;
    children: Snippet;
  }

  let { href, active = false, title, class: className = "", children }: Props = $props();

  function onClick(event: MouseEvent) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  }
</script>

<a
  {href}
  {title}
  class={className}
  aria-current={active ? "page" : undefined}
  onclick={onClick}
>
  {@render children()}
</a>
