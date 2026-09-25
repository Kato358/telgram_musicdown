<script lang="ts">
  /** 下载队列卡（设计规范 §5.7，v3.9 加内联入队区）：主栏里正在跑的任务 + 入队 + 清空。
   *
   * 队列是这一页的控制面——每一项都能暂停/继续/取消，所以它不用表格列（没有列头），
   * 用「每项一块淡底行」。`composer` 是卡体顶部的常驻链接输入框（v3.9：贴进去回车即入队，
   * 不必先点开弹窗）；卡头右侧是两个次级动作：批量粘贴（弹窗，一行一条）与清空队列
   * （取消还在等待的项；没有等待项时按钮就是灰的，不做点了没反应的按钮）。
   */
  import ListMusicIcon from "@lucide/svelte/icons/list-music";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import TrashIcon from "@lucide/svelte/icons/trash-2";
  import type { Snippet } from "svelte";
  import type { TaskRow } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
  import type { TaskReadings } from "$lib/stores/queue.svelte";
  import { Button } from "$lib/components/ui/button";
  import EmptyState from "./EmptyState.svelte";
  import QueueRow from "./QueueRow.svelte";
  import SectionCard from "./SectionCard.svelte";
  import type { TaskAction } from "./TaskRow.svelte";

  interface Props {
    /** 进行中的任务（等待 + 下载中 + 已暂停），顺序由调用方定。 */
    tasks: TaskRow[];
    /** 按行取实时读数（统一走 `queue.readings()`，视图不自己拼帧）。 */
    readings: (task: TaskRow) => TaskReadings;
    /** 还在等待的项数：为 0 时「清空队列」不可点。 */
    queued: number;
    onact: (taskId: number, action: TaskAction) => void;
    onadd: () => void;
    onclear: () => void;
    /** 卡体顶部的常驻入队区（内联链接输入框）；给了就渲染在任务列表之上。 */
    composer?: Snippet;
  }

  let { tasks, readings, queued, onact, onadd, onclear, composer }: Props = $props();
</script>

<SectionCard
  title={t("downloads.queueTitle")}
  hint={t("downloads.queueHint")}
  icon={ListMusicIcon}
  badge={tasks.length}
>
  {#snippet actions()}
    <Button variant="outline" size="sm" disabled={queued === 0} onclick={onclear}>
      <TrashIcon aria-hidden="true" />
      {t("downloads.clearQueue")}
    </Button>
    <Button variant="outline" size="sm" onclick={onadd}>
      <PlusIcon aria-hidden="true" />
      {t("downloads.addLink")}
    </Button>
  {/snippet}

  <div class="flex flex-col gap-3">
    {#if composer}
      {@render composer()}
    {/if}

    {#if tasks.length === 0}
      <EmptyState bare icon={ListMusicIcon} title={t("downloads.queueEmpty")} />
    {:else}
      <ul class="flex flex-col gap-2">
        {#each tasks as task (task.id)}
          <QueueRow {task} progress={readings(task)} onact={(action) => onact(task.id, action)} />
        {/each}
      </ul>
    {/if}
  </div>
</SectionCard>
