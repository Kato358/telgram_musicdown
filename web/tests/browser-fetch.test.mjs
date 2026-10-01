// 前端回归测试（node:assert + 源码契约）：浏览器下载「取回中」任务卡（FR-DL-08，v3.28）。
//
// 锁住四件容易被无意改回去的事：
//   1. 进度是**真的**——store 消费服务端那条 SSE，卡片把字节比例喂给 ProgressBar；
//   2. 总量报不出来时才退不确定态（不是一上来就给个转圈）；
//   3. 取消键必须在 aria-hidden 子树**之外**（否则读屏用户没法取消）；
//   4. 导航前先 HEAD 探一次——404 不能再把整页带到浏览器的错误页上。
// 用法：node tests/browser-fetch.test.mjs（在 web/ 目录下）。

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, "..", rel), "utf8");

const cardSrc = read("src/lib/components/app/BrowserFetchCard.svelte");
const storeSrc = read("src/lib/stores/search.svelte.ts");
const messagesSrc = read("src/lib/i18n/messages.ts");

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

// ---- 1. 真进度：store 消费 SSE，卡片把比例喂给进度条 ----
run("store 用 SSE 流消费取数进度（不再是等取完的一次性 POST）", () => {
  assert.ok(
    storeSrc.includes("ssePost<BrowserDownloadEvent>"),
    "browserDownload 应走 ssePost 消费进度帧",
  );
  // 精确到调用本身：窗口式正则会被 prettier 的换行搞脆
  assert.ok(
    storeSrc.includes("this.patchBrowserJob(key, event.loaded"),
    "收到 preparing 帧应把字节进度写进作业（patchBrowserJob）",
  );
  assert.ok(
    storeSrc.includes("timeoutMs: BROWSER_DOWNLOAD_TIMEOUT_MS"),
    "这条流应带自己的空闲超时（默认 15s 对取数太短）",
  );
});

run("卡片把真实比例交给 ProgressBar，而不是写死的转圈", () => {
  assert.ok(cardSrc.includes("<ProgressBar ratio={ratioOf(job)}"), "ProgressBar 应吃 ratioOf(job)");
  const ratioOf = cardSrc.match(/function ratioOf[\s\S]*?\n {2}\}/);
  assert.ok(ratioOf, "ratioOf 应存在");
  assert.ok(
    ratioOf[0].includes("job.total === null"),
    "只有总量未知时才该给 null（不确定态），否则必须给真实比例",
  );
  assert.ok(ratioOf[0].includes("job.loaded / job.total"), "真实比例来自已写字节 / 总量");
});

// ---- 2. 取消入口：在 aria-hidden 之外 ----
run("取消键存在且不在 aria-hidden 子树里", () => {
  const cancelAt = cardSrc.indexOf("cancelBrowserDownload");
  const hiddenAt = cardSrc.indexOf('<div aria-hidden="true"');
  assert.ok(cancelAt >= 0, "卡片应调用 search.cancelBrowserDownload");
  assert.ok(hiddenAt >= 0, "会随秒变化的读数应整块 aria-hidden");
  assert.ok(cancelAt < hiddenAt, "取消键必须排在 aria-hidden 块之前（藏了就读不到也点不到）");
});

run("取消真的中止取数（abort 那个 controller）", () => {
  const fn = storeSrc.match(/cancelBrowserDownload\(key: string\)[\s\S]*?\n {2}\}/);
  assert.ok(fn, "cancelBrowserDownload 应存在");
  assert.ok(fn[0].includes(".abort()"), "取消必须 abort 掉那条流");
  assert.ok(storeSrc.includes("new AbortController()"), "browserDownload 应持有取消柄");
});

// ---- 3. 导航前的可用性探测 ----
run("assign 之前先 HEAD 探测，404 不导航", () => {
  const handOff = storeSrc.match(/private async handOffToBrowser[\s\S]*?\n {2}\}/);
  assert.ok(handOff, "handOffToBrowser 应存在");
  const body = handOff[0];
  const headAt = body.indexOf("api.head(");
  const assignAt = body.indexOf("window.location.assign");
  assert.ok(headAt >= 0, "应调用 api.head 探测附件可用性");
  assert.ok(assignAt >= 0, "ready 之后仍要交给浏览器下载");
  assert.ok(headAt < assignAt, "探测必须排在导航之前（之后就读不到 404 了）");
  assert.ok(body.includes("catch"), "探测失败（过期）要就地转成行内原因");
  assert.ok(
    body.includes("abort.signal.aborted"),
    "取消恰好落在「已就绪」与「开始导航」之间时，不该照旧导航",
  );
});

// ---- 4. 文案：两种语言都得有（缺键会回落显示键名） ----
run("取消 / 百分比 / 过期 / 分秒 四组文案 zh 与 en 都在", () => {
  for (const key of [
    "browserFetchCancel",
    "browserFetchStatusPercent",
    "browserFetchElapsedMin",
    "browserFetchExpired",
  ]) {
    const hits = messagesSrc.match(new RegExp(`${key}:`, "g")) ?? [];
    assert.equal(hits.length, 2, `${key} 应在 zh-CN 与 en 各出现一次（实际 ${hits.length} 次）`);
  }
});

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
