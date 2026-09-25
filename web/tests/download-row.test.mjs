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

// ---- 1. Hover Action 可见性：桌面端不悬浮也可见 ----
run("次级动作不再被 opacity-0 藏到 hover 后", () => {
  const hover = downloadRowSrc.match(/const HOVER_ACTION =\s*"([^"]+)"/);
  assert.ok(hover, "DownloadRow.svelte 应定义 HOVER_ACTION 类串");
  const cls = hover[1];
  assert.ok(!cls.includes("md:opacity-0"), `桌面端不能 opacity-0（得到：${cls}）`);
  assert.ok(!cls.includes("opacity-0"), "任何端都不该默认隐形（触屏也曾受影响）");
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
    /async function deleteRow[\s\S]*?\n  \}/,
  );
  assert.ok(deleteRow, "deleteRow 函数存在");
  assert.ok(
    !deleteRow[0].includes("/api/downloads/"),
    "行删除不该走任务级端点（记录可能没有活着的任务）",
  );
});

// ---- 3. 删除二次确认弹窗 ----
run("行删除先弹确认弹窗（confirmOpen），不再直接删", () => {
  assert.ok(downloadsViewSrc.includes("confirmOpen"), "应有 confirmOpen 状态");
  const deleteTrigger = downloadsViewSrc.match(/ondelete=\{\(\) => [^}]+\}/);
  assert.ok(deleteTrigger, "ondelete 处理器存在");
  assert.ok(
    deleteTrigger[0].includes("confirmOpen") || deleteTrigger[0].includes("deleteTarget"),
    `ondelete 应只打开确认弹窗（得到：${deleteTrigger[0]}）`,
  );
});

run("确认弹窗含取消与确认删除两个按钮", () => {
  const dialog = downloadsViewSrc.match(/confirmOpen[\s\S]*?<\/Dialog>/);
  assert.ok(dialog, "应有绑定 confirmOpen 的 Dialog");
  assert.ok(dialog[0].includes('t("common.cancel")'), "弹窗应有取消按钮");
  assert.ok(dialog[0].includes("deleteTarget"), "确认按钮应作用于 deleteTarget");
});

run("弹窗里确认后调用 deleteRow 并关闭弹窗", () => {
  const dialog = downloadsViewSrc.match(/<Dialog\s+bind:open=\{confirmOpen\}[\s\S]*?<\/Dialog>/);
  assert.ok(dialog, "应有绑定 confirmOpen 的 Dialog");
  assert.ok(dialog[0].includes("void confirmDelete()"), "确认按钮应调用 confirmDelete");
});

run("deleteRow 完成后清空 deleteTarget", () => {
  const fn = downloadsViewSrc.match(
    /async function deleteRow[\s\S]*?\n  \}\r?\n\r?\n  async function confirmDelete/,
  );
  assert.ok(fn, "deleteRow 应在 confirmDelete 之前且完整");
  assert.ok(downloadsViewSrc.includes("deleteTarget = null"), "执行后应清空 deleteTarget");
  const confirm = downloadsViewSrc.match(/async function confirmDelete[\s\S]*?\n  \}/);
  assert.ok(confirm, "confirmDelete 存在");
  assert.ok(confirm[0].includes("deleteRow(deleteTarget)"), "确认后应作用于 deleteTarget");
});

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
