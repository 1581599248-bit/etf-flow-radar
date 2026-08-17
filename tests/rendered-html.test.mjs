import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("dashboard source retains the answer-first research modules", async () => {
  const page = await readFile("site/index.html", "utf8");
  const css = await readFile("site/styles.css", "utf8");
  assert.match(page, /资金ETF流动每日跟踪/);
  assert.match(page, /主要宽基数据摘要/);
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /申万一级与主流行业资金坐标/);
  assert.match(page, /申万行业分类标准2021版/);
  assert.match(page, /热门主题/);
  assert.match(page, /当日ETF流入流出分布/);
  assert.match(page, /数据解读/);
  assert.match(page, /ETF跟踪观点/);
  assert.match(page, /导出高清 JPG/);
  assert.match(page, /全量ETF每日变更检查/);
  assert.doesNotMatch(page, /国家队代理ETF净流入|代理池当日估算净流入/);
  assert.match(css, /\.e-row\[hidden\]\{display:none!important\}/);
});

test("schema v6 separates primary subscription flow, secondary order flow and market scopes", async () => {
  const snapshot = JSON.parse(await readFile("site/data/latest.json", "utf8"));
  assert.equal(snapshot.schemaVersion, 6);
  assert.equal(snapshot.sourceMode, "REAL");
  assert.ok(["verified", "warning"].includes(snapshot.status));
  assert.ok(snapshot.quality.officialSessions >= 21);
  assert.equal(snapshot.quality.flowModelVersion, 2);
  assert.equal(snapshot.quality.canonicalFlowValuation, "sameDayUnitNAV");
  assert.equal(snapshot.quality.metricSeparation, "primary_market_subscription_vs_secondary_market_order_flow");

  const primary = snapshot.flowMetrics.primaryMarket;
  assert.equal(primary.metric, "primaryMarketNetSubscriptionEstimate");
  assert.equal(primary.valuation, "sameDayUnitNAV");
  assert.deepEqual(
    Object.keys(primary.scopeTotals).sort(),
    ["aShareStockEtf", "allEtf", "stockEtfIncludingCrossBorder"].sort(),
  );
  assert.equal(snapshot.market.metric, primary.metric);
  assert.equal(snapshot.market.valuation, primary.valuation);
  assert.equal(snapshot.market.scopeKey, "aShareStockEtf");
  assert.equal(snapshot.market.flow1d, primary.scopeTotals.aShareStockEtf.flow1d);
  assert.equal(snapshot.market.etfCount, primary.scopeTotals.aShareStockEtf.etfCount);
  assert.equal(
    snapshot.market.increaseEtfCount1d + snapshot.market.decreaseEtfCount1d + snapshot.market.unchangedEtfCount1d,
    snapshot.market.etfCount,
  );

  const secondary = snapshot.flowMetrics.secondaryMarketOrderFlow;
  assert.equal(secondary.metric, "secondaryMarketMainOrderFlow");
  assert.match(secondary.definition, /不是ETF申购赎回/);
  assert.ok(["available", "unavailable"].includes(secondary.status));

  assert.equal(snapshot.universe.length, snapshot.quality.marketEtfCount);
  assert.equal(snapshot.quality.completeUniverseCount, snapshot.quality.marketEtfCount);
  assert.ok(snapshot.universeAudit);
  assert.ok(snapshot.universe.every((row) => "assetScope" in row));
  assert.equal(new Set(snapshot.etfs.map((row) => row.code)).size, snapshot.quality.classifiedEtfCount);
  for (const row of snapshot.etfs) {
    assert.equal(row.flowMetric, "primaryMarketNetSubscriptionEstimate");
    assert.equal(row.flowValuation, "sameDayUnitNAV");
    assert.equal(typeof row.shareDelta1d, "number");
    assert.equal(typeof row.nav, "number");
  }

  assert.ok(snapshot.groups.some((row) => row.kind === "broad"));
  assert.ok(snapshot.groups.some((row) => row.kind === "style"));
  assert.ok(snapshot.groups.some((row) => row.kind === "industry"));
  for (const row of snapshot.groups) {
    assert.equal(typeof row.flow1d, "number");
    assert.equal(typeof row.flow5d, "number");
    assert.equal(typeof row.flow20d, "number");
    assert.equal(row.flow5dMetric, "endpointShareChangeTimesCurrentNAV");
    assert.equal(row.flow20dMetric, "endpointShareChangeTimesCurrentNAV");
  }

  const marketRecon = snapshot.quality.marketScopeReconciliation;
  assert.equal(marketRecon.aShareEquityPrimaryFlow1d, snapshot.market.flow1d);
  assert.ok(Number.isFinite(marketRecon.classifiedGroupPrimaryFlow1d));
  assert.ok(Number.isFinite(marketRecon.ungroupedDifference));

  assert.ok(Array.isArray(snapshot.industryRollups));
  assert.ok(snapshot.industryRollups.length > 0 && snapshot.industryRollups.length <= 31);
  assert.ok(snapshot.industryRollups.every((row) => row.kind === "industryRollup"));
  assert.ok(snapshot.industryRollups.some((row) => row.name === "电子"));
  assert.ok(snapshot.industryRollups.some((row) => row.name === "非银金融"));
  assert.ok(!snapshot.industryRollups.some((row) => row.name === "半导体"));

  assert.match(snapshot.methodology.identity, /禁止据此推断/);
  assert.match(snapshot.methodology.flow, /T日单位净值/);
  assert.match(snapshot.methodology.metricSeparation, /两个不同变量/);
  assert.match(snapshot.methodology.multiDay, /不是逐日净申购额之和/);
  assert.match(snapshot.methodology.scope, /全部ETF、股票ETF（含跨境）和A股股票ETF/);

  const daily = JSON.parse(await readFile(`site/data/daily/${snapshot.tradeDate}.json`, "utf8"));
  assert.equal(daily.metric, primary.metric);
  assert.equal(daily.valuation, primary.valuation);
  assert.equal(daily.tradeDate, snapshot.tradeDate);
});

test("generated client makes the metric distinction visible", async () => {
  const page = await readFile("dist/index.html", "utf8");
  const snapshot = JSON.parse(await readFile("dist/data/latest.json", "utf8"));
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /A股股票ETF一级市场净申赎/);
  assert.match(page, /股票ETF（含跨境）· 一级市场/);
  assert.match(page, /A股股票ETF · 二级市场主力资金/);
  assert.match(page, /成交订单流，不等于申购赎回/);
  assert.match(page, /5日端点资金变化/);
  assert.doesNotMatch(page, /5日累计资金变化/);
  assert.equal(snapshot.schemaVersion, 6);
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
