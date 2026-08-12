import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("production bundle contains the real-data research product", async () => {
  const manifest = JSON.parse(await readFile("dist/client/.vite/manifest.json", "utf8"));
  const pageChunk = manifest["app/page.tsx"]?.file;
  assert.ok(pageChunk, "page bundle should be discoverable from the build manifest");
  const bundle = await readFile(`dist/client/${pageChunk}`, "utf8");
  assert.match(bundle, /资金ETF流动每日跟踪/);
  assert.match(bundle, /资金强度.*申赎广度/);
  assert.match(bundle, /不能确认/);
  assert.match(bundle, /不构成投资建议/);
  assert.match(bundle, /删除“国家队代理资金”/);
  assert.doesNotMatch(bundle, /国家队代理 ETF 净流|代理池当日估算净流/);
});

test("static bundle and Render blueprint are aligned", async () => {
  const page = await readFile("pages-dist/index.html", "utf8");
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /资金强度 × 申赎广度/);
  assert.match(page, /不能概括为“资金偏向大盘”/);
  assert.match(page, /删除“国家队代理资金”/);
  assert.doesNotMatch(page, /国家队代理 ETF 净流|代理池当日估算净流/);
  assert.match(blueprint, /staticPublishPath: \.\/pages-dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
