// 前端回归测试（node:assert + 源码契约）：搜索页的输入框不能被 URL 里的 ?q= 回灌。
//
// 症状（修复前，真实可复现）：在 /search?q=Aimer 上敲任意键 → 消费 ?q= 的 effect 重跑
// （它读了 `search.query`，也就是输入框绑定的那个值）→ 用 URL 里那个旧关键词覆写
// `search.query` 并触发一次搜索 —— 输入框改不动、每按一键都被重置并刷新结果。
//
// 契约（两条，都是这条链路上真实踩过的）：
//   1. 消费 ?q= 的 effect 里**不许读** `search.query`（只许写）：读它 = 把用户输入当依赖；
//   2. 「已消费过的查询串」记在普通变量里，且只在 URL 查询串真的变了时才消费。
// 用法：node tests/search-input.test.mjs（在 web/ 目录下）。

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, "..", rel), "utf8");

const searchViewSrc = read("src/views/SearchView.svelte");

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

/** 取出消费 URL 查询串的那个 effect 块（SearchView 里只有它读 router.search / router.query）。 */
function queryEffect() {
  const blocks = searchViewSrc.match(/\$effect\(\(\) => \{[\s\S]*?\n {2}\}\);/g) ?? [];
  const found = blocks.find(
    (block) => block.includes("router.search") || block.includes("router.query"),
  );
  assert.ok(found, "SearchView 里应有一个消费 URL 查询串的 effect");
  return found;
}

run("消费 ?q= 的 effect 只读 router.search，不读 search.query", () => {
  const effect = queryEffect();
  // 写入是允许的（把 URL 关键词放进输入框）；把写入摘掉后不许再出现读
  const withoutWrite = effect.replace(/search\.query\s*=\s*keyword\s*;?/g, "");
  assert.ok(
    !withoutWrite.includes("search.query"),
    `effect 里读了 search.query（用户每敲一键都会被 URL 回灌）：\n${effect}`,
  );
});

run("消费入口仍按 URL 关键词写入并搜一次（没有把功能改没）", () => {
  const effect = queryEffect();
  assert.ok(effect.includes("search.query = keyword"), "应把 URL 关键词写进输入框");
  assert.ok(effect.includes("search.runSearch()"), "应按 URL 关键词搜一次");
});

run("已消费的查询串记在非 $state 变量里", () => {
  assert.ok(
    /let handledQuery = ""/.test(searchViewSrc),
    "应有 handledQuery 记录已消费的 URL 查询串",
  );
  assert.ok(
    !/let handledQuery = \$state/.test(searchViewSrc),
    "handledQuery 必须是普通变量：$state 会重新进依赖，等于没修",
  );
  const effect = queryEffect();
  assert.ok(
    effect.includes("handledQuery"),
    "消费判据应比对 URL 查询串本身（而不是拿输入框的值去比）",
  );
});

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
