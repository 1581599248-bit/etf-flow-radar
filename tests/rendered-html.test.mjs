import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("static source keeps the evidence-first analysis and identity boundary", async () => {
  const page = await readFile("site/index.html", "utf8");
  assert.match(page, /资金ETF流动每日跟踪/);
  assert.match(page, /资金强度 × 申赎广度/);
  assert.match(page, /不能确认/);
  assert.match(page, /删除“国家队代理资金”/);
  assert.doesNotMatch(page, /国家队代理ETF净流入|代理池当日估算净流入/);
});

test("generated output and Render blueprint use one reproducible directory", async () => {
  const page = await readFile("dist/index.html", "utf8");
  const snapshot = JSON.parse(await readFile("dist/data/latest.json", "utf8"));
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /资金强度 × 申赎广度/);
  assert.equal(snapshot.schemaVersion, 3);
  assert.equal(snapshot.sourceMode, "REAL");
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
