<script lang="ts">
  /** 右栏「下载进度」（设计规范 §5.7）：队列的窄镜像。
   *
   * 与主栏「下载队列」同一份 `queue` 快照、同一套读数，只换排布（一行一项、图标动作）——
   * 它不是第二份事实源，而是让「现在在传什么」在视线右侧一直看得见。
   * 「查看全部」固定出现在卡头（参考图的做法）：主栏的队列卡是全量视图，锚点跳过去。
   */
  import ZapIcon from "@lucide/svelte/icons/zap";
  import type { TaskRow } from "$lib/api/types";
  import { t } from "$lib/i18n/index.svelte";
  import type { TaskReadings } from "$lib/stores/queue.svelte";
  import QueueRow from "./QueueRow.svelte";
  import SectionCard from "./SectionCard.svelte";
  import type { TaskAction } from "./TaskRow.svelte";

  /** 窄卡里放几项；其余走「查看全部」。 */
  const SHOWN = 3;

  interface Props {
    tasks: TaskRow[];
    readings: (task: TaskRow) => TaskReadings;
    onact: (taskId: number, action: TaskAction) => void;
  }

  let { tasks, readings, onact }: Props = $props();

  const shown = $derived(tasks.slice(0, SHOWN));
</script>

<SectionCard title={t("downloads.progressTitle")} icon={ZapIcon} dense>
  {#snippet actions()}
    <a href="#queue" class="text-caption text-primary hover:text-primary-hover">
      {t("dashboard.viewAll")}
    </a>
  {/snippet}

  {#if shown.length === 0}
    <p class="text-caption text-muted-foreground">{t("downloads.queueEmpty")}</p>
  {:else}
    <ul class="flex flex-col gap-2">
      {#each shown as task (task.id)}
        <QueueRow
          {task}
          density="compact"
          progress={readings(task)}
          onact={(action) => onact(task.id, action)}
        />
      {/each}
    </ul>
  {/if}
</SectionCard>
