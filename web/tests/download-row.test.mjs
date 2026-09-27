// 前端回归测试（node:assert + 源码契约）：下载页行内按钮的可见性与删除确认流程。
//
// 项目没有 JS 测试框架；这里按 TDD 用源码契约锁住本次修复的三个行为：
//   1. 次级动作（重试/取消/删除）在桌面端不悬浮也可见（不能 opacity-0 藏在 hover 后）；
//   2. 行删除走记录级 DELETE /api/history/{id}（不是任务级 DELETE /api/downloads/{task_id}）；
//   3. 删除先弹确认弹窗（confirmOpen 状态 + 二次确认后才调用删除）。
// 用法：node tests/download-row.test.mjs（在 web/ 目录下）。

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, "..", rel), "utf8");

const downloadRowSrc = read("src/lib/components/app/DownloadRow.svelte");
const downloadsViewSrc = read("src/views/DownloadsView.svelte");

function test(name, fn) {
  try {
    fn();
    console.log(`  ok - ${name}`);
    return true;
  } catch (err) {
    console.error(`  FAIL - ${name}`);
    console.error(`    ${err.message}`);
    return false;
  }
}

let pass = 0;
let fail = 0;
function run(name, fn) {
  if (test(name, fn)) pass += 1;
  else fail += 1;
}

// ---- 1. 动作可见性：桌面端不悬浮也可见 ----
// v3.11 起次级动作与播放键同规格，公共基类为 ACTION_BASE（原 HOVER_ACTION 已重命名）。
run("次级动作不再被 opacity-0 藏到 hover 后", () => {
  const base = downloadRowSrc.match(/const ACTION_BASE =\s*[\s\S]*?"([^"]*)";/);
  assert.ok(base, "DownloadRow.svelte 应定义 ACTION_BASE 类串");
  const cls = base[1];
  assert.ok(!cls.includes("opacity-0"), `任何端都不该默认隐形（得到：${cls}）`);
  // 三类次级动作都由 ACTION_BASE 派生 → 继承「常驻可见」
  for (const name of ["RETRY_ACTION", "CANCEL_ACTION", "DELETE_ACTION"]) {
    const m = downloadRowSrc.match(new RegExp(`const ${name} = \`\\$\\{ACTION_BASE\\}`));
    assert.ok(m, `${name} 应由 ACTION_BASE 派生（否则可能各自藏回 hover）`);
  }
});

run("次级动作按钮存在 aria-label（重试/取消/删除）", () => {
  for (const label of ['t("tasks.retry")', 't("tasks.cancel")', 't("downloads.delete")']) {
    assert.ok(downloadRowSrc.includes(label), `缺少 ${label}`);
  }
});

// ---- 2. 行删除走记录级端点 ----
run("deleteRow 调用 DELETE /api/history/{id}（记录级）", () => {
  assert.ok(
    downloadsViewSrc.includes("api.delete(`/api/history/${row.id}`)"),
    "deleteRow 应调用记录级端点",
  );
  const deleteRow = downloadsViewSrc.match(
    /async function deleteRow[\s\S]*?\n {2}\}/,
  );
  assert.ok(deleteRow, "deleteRow 函数存在");
  assert.ok(
    !deleteRow[0].includes("/api/downloads/"),
    "行删除不该走任务级端点（记录可能没有活着的任务）",
  );
});

// ---- 3. 删除二次确认弹窗 ----
// v3.23：弹窗本体收进 ConfirmDialog.svelte（设计规范 §5.9），视图里不再有 Dialog 标记，
// 故原来的两条「弹窗里有什么按钮」断言随切口删除（它们锁的是被删掉的模板字面量，其中一条
// 还会去匹配隔壁「粘贴链接」弹窗而假通过）。不去按新模板再写一条同形状的断言——**模板不是契约**，
// 「二次确认真的挡住了删除」由下面这条守：ondelete 只开门，不删数据。
run("行删除先弹确认弹窗（confirmOpen），不再直接删", () => {
  assert.ok(downloadsViewSrc.includes("confirmOpen"), "应有 confirmOpen 状态");
  const deleteTrigger = downloadsViewSrc.match(/ondelete=\{\(\) => [^}]+\}/);
  assert.ok(deleteTrigger, "ondelete 处理器存在");
  assert.ok(
    deleteTrigger[0].includes("confirmOpen") || deleteTrigger[0].includes("deleteTarget"),
    `ondelete 应只打开确认弹窗（得到：${deleteTrigger[0]}）`,
  );
});

run("deleteRow 完成后清空 deleteTarget", () => {
  const fn = downloadsViewSrc.match(
    /async function deleteRow[\s\S]*?\n {2}\}\r?\n\r?\n {2}async function confirmDelete/,
  );
  assert.ok(fn, "deleteRow 应在 confirmDelete 之前且完整");
  assert.ok(downloadsViewSrc.includes("deleteTarget = null"), "执行后应清空 deleteTarget");
  const confirm = downloadsViewSrc.match(/async function confirmDelete[\s\S]*?\n {2}\}/);
  assert.ok(confirm, "confirmDelete 存在");
  assert.ok(confirm[0].includes("deleteRow(deleteTarget)"), "确认后应作用于 deleteTarget");
});

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
