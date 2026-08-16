import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("dashboard contains the answer-first modules and audited ETF evidence", async () => {
  const page = await readFile("site/index.html", "utf8");
  const css = await readFile("site/styles.css", "utf8");
  assert.match(page, /资金ETF流动每日跟踪/);
  assert.match(page, /主要宽基数据摘要/);
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /申万一级与主流行业资金坐标/);
  assert.match(page, /申万行业分类标准2021版/);
  assert.match(page, /热门主题/);
  assert.match(page, /const groupTag=r=>r\.kind==="industry"&&r\.parent\?"热门主题":kindName\[r\.kind\]/);
  assert.match(page, /AVG:"成交均价"/);
  assert.match(page, /收益口径/);
  assert.match(page, /20日相对沪深300收益/);
  assert.match(page, /5日资金变化率（占5日前规模）/);
  assert.match(page, /当日ETF流入流出分布/);
  assert.match(page, /数据解读/);
  assert.match(page, /ETF跟踪观点/);
  assert.match(page, /下一交易日盘前使用/);
  assert.match(page, /导出高清 JPG/);
  assert.match(page, /全量ETF每日变更检查/);
  assert.match(page, /交易所完整ETF/);
  assert.match(page, /组内ETF参考规模/);
  assert.match(page, /layoutBubbleLabels/);
  assert.match(page, /全部标签就近避让/);
  assert.match(page, /全部资金观察组证据表/);
  assert.match(page, /当日流入领跑 \/ 流出领跑/);
  assert.doesNotMatch(page, /国家队代理ETF净流入|代理池当日估算净流入/);
  assert.doesNotMatch(page, /我们怎么看|识别边界|已完成分析的A股股票ETF/);
  assert.match(css, /\.e-row\[hidden\]\{display:none!important\}/);
});

test("verified snapshot separates market scope, classification and SW level-one rollups", async () => {
  const snapshot = JSON.parse(await readFile("site/data/latest.json", "utf8"));
  assert.ok(snapshot.schemaVersion >= 5);
  assert.equal(snapshot.sourceMode, "REAL");
  assert.equal(snapshot.status, "verified");
  assert.ok(snapshot.quality.officialSessions >= 21);
  assert.ok(snapshot.quality.classifiedEtfCount >= 300);
  assert.ok(snapshot.quality.marketScopeEtfCount >= snapshot.quality.classifiedEtfCount);
  assert.equal(snapshot.market.etfCount, snapshot.quality.marketScopeEtfCount);
  assert.equal(
    snapshot.market.increaseEtfCount1d + snapshot.market.decreaseEtfCount1d + snapshot.market.unchangedEtfCount1d,
    snapshot.market.etfCount,
  );

  assert.equal(snapshot.universe.length, snapshot.quality.marketEtfCount);
  assert.equal(snapshot.quality.completeUniverseCount, snapshot.quality.marketEtfCount);
  assert.ok(snapshot.universeAudit);
  assert.equal(new Set(snapshot.etfs.map((row) => row.code)).size, snapshot.quality.classifiedEtfCount);

  assert.ok(snapshot.groups.some((row) => row.kind === "broad"));
  assert.ok(snapshot.groups.some((row) => row.kind === "style"));
  assert.ok(snapshot.groups.some((row) => row.kind === "industry"));
  assert.ok(snapshot.groups.every((row) => row.kind !== "theme"));
  for (const row of snapshot.groups) {
    assert.equal(typeof row.flow1d, "number");
    assert.equal(typeof row.flow5d, "number");
    assert.equal(typeof row.flow20d, "number");
    assert.equal(typeof row.flowIntensity5dPct, "number");
    assert.equal(row.increaseEtfCount5d + row.decreaseEtfCount5d + row.unchangedEtfCount5d, row.etfCount);
  }

  const groupRecon = snapshot.quality.reconciliation;
  assert.equal(groupRecon.groupEtfCountTotal, snapshot.quality.classifiedEtfCount);
  assert.equal(groupRecon.uniqueAnalyzedEtfCount, snapshot.quality.classifiedEtfCount);
  assert.ok(Math.abs(groupRecon.flowDifference) <= 0.5);
  const marketRecon = snapshot.quality.marketScopeReconciliation;
  assert.equal(marketRecon.aShareEquityMarketFlow1d, snapshot.market.flow1d);
  assert.ok(Number.isFinite(marketRecon.classifiedGroupFlow1d));
  assert.ok(Number.isFinite(marketRecon.ungroupedDifference));

  assert.ok(Array.isArray(snapshot.industryRollups));
  assert.ok(snapshot.industryRollups.length > 0 && snapshot.industryRollups.length <= 31);
  assert.ok(snapshot.industryRollups.every((row) => row.kind === "industryRollup"));
  assert.ok(snapshot.industryRollups.some((row) => row.name === "电子"));
  assert.ok(snapshot.industryRollups.some((row) => row.name === "非银金融"));
  assert.ok(!snapshot.industryRollups.some((row) => row.name === "半导体"));
  assert.equal(snapshot.quality.industryRollupCount, snapshot.industryRollups.length);
  assert.ok(Array.isArray(snapshot.themeGroups));
  assert.equal(snapshot.quality.themeGroupCount, snapshot.themeGroups.length);

  assert.match(snapshot.methodology.identity, /禁止据此推断/);
  assert.match(snapshot.methodology.flow, /交易所日终份额为主源/);
  assert.match(snapshot.methodology.multiDay, /端点份额变化估算/);
  assert.match(snapshot.methodology.multiDay, /不等同逐日资金流之和/);
  assert.match(snapshot.methodology.scope, /市场总量不依赖行业\/主题分类/);
  assert.match(snapshot.methodology.classification, /申万一级行业与热门主题/);
});

test("generated output and Render blueprint share one reproducible directory", async () => {
  const page = await readFile("dist/index.html", "utf8");
  const snapshot = JSON.parse(await readFile("dist/data/latest.json", "utf8"));
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /申万一级与主流行业资金坐标/);
  assert.ok(snapshot.schemaVersion >= 5);
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
