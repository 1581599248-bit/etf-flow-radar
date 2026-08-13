import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("dashboard contains the answer-first modules and two coordinate maps", async () => {
  const page = await readFile("site/index.html", "utf8");
  assert.match(page, /资金ETF流动每日跟踪/);
  assert.match(page, /主要宽基数据摘要/);
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /行业板块资金坐标/);
  assert.match(page, /20日相对沪深300收益/);
  assert.match(page, /5日资金变化率（占5日前规模）/);
  assert.match(page, /当日ETF增减分布/);
  assert.match(page, /数据解读/);
  assert.match(page, /ETF跟踪观点/);
  assert.match(page, /下一交易日盘前使用/);
  assert.doesNotMatch(page, /我们怎么看|识别边界| bp|观察池当日估算资金变化|已通过门禁|滚动\$\{data\.quality\.officialSessions\}/);
  assert.match(page, /导出高清 JPG/);
  assert.match(page, /全量ETF每日变更检查/);
  assert.match(page, /交易所完整ETF/);
  assert.doesNotMatch(page, /国家队代理ETF净流入|代理池当日估算净流入/);
});

test("verified snapshot is internally coherent and carries the complete ETF universe", async () => {
  const snapshot = JSON.parse(await readFile("site/data/latest.json", "utf8"));
  assert.ok(snapshot.schemaVersion >= 4);
  assert.equal(snapshot.sourceMode, "REAL");
  assert.equal(snapshot.status, "verified");
  assert.ok(snapshot.quality.officialSessions >= 21);
  assert.ok(snapshot.quality.classifiedEtfCount >= 300);
  assert.ok(snapshot.groups.some((row) => row.kind === "broad"));
  assert.ok(snapshot.groups.some((row) => row.kind === "style"));
  assert.ok(snapshot.groups.some((row) => row.kind === "sector"));
  assert.match(snapshot.methodology.identity, /禁止据此推断/);
  if (snapshot.schemaVersion >= 5) {
    assert.equal(snapshot.universe.length, snapshot.quality.marketEtfCount);
    assert.equal(snapshot.quality.completeUniverseCount, snapshot.quality.marketEtfCount);
    assert.ok(snapshot.universeAudit);
  }
  for (const row of snapshot.groups) {
    assert.equal(typeof row.flow1d, "number");
    assert.equal(typeof row.flow5d, "number");
    assert.equal(typeof row.flow20d, "number");
    assert.equal(typeof row.flowIntensity5dPct, "number");
    assert.equal(row.increaseEtfCount5d + row.decreaseEtfCount5d + row.unchangedEtfCount5d, row.etfCount);
  }
});

test("generated output and Render blueprint share one reproducible directory", async () => {
  const page = await readFile("dist/index.html", "utf8");
  const snapshot = JSON.parse(await readFile("dist/data/latest.json", "utf8"));
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /宽基与风格资金坐标/);
  assert.ok(snapshot.schemaVersion >= 4);
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
