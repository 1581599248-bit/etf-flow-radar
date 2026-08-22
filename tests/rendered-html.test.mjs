import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// 校验原则：文案中的数字与方向必须能对回快照数据；
// 强度形容词（小幅/明显/大幅/偏强/明显占优）随阈值调优可能变化，不写死。
const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test("dashboard source retains the answer-first research modules", async () => {
  const page = await readFile("site/index.html", "utf8");
  const css = await readFile("site/styles.css", "utf8");
  assert.match(page, /资金ETF流动逐日跟踪/);
  assert.match(page, /主要宽基数据摘要/);
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /申万一级与主流行业资金坐标/);
  assert.match(page, /申万行业分类标准2021版/);
  assert.match(page, /热门主题/);
  assert.match(page, /当日ETF流入流出分布/);
  assert.doesNotMatch(page, /数据解读/);
  assert.match(page, /ETF跟踪观点/);
  assert.match(page, /导出完整 JPG/);
  assert.match(page, /导出结论 JPG/);
  assert.match(page, /全量ETF逐日变更检查/);
  assert.match(page, /【宽基份额】/);
  assert.match(page, /【风格份额】/);
  assert.match(page, /【申万一级与主题行业份额】/);
  assert.match(page, /【单只ETF份额大额变化】/);
  assert.doesNotMatch(page, /国家队代理ETF净流入|代理池当日估算净流入/);
  assert.match(css, /\.e-row\[hidden\]\{display:none!important\}/);
});

test("schema v6 separates primary subscription flow, secondary trading flow and reconciles every client layer", async () => {
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
  assert.deepEqual(Object.keys(primary.scopeTotals).sort(), ["aShareStockEtf", "allEtf", "stockEtfIncludingCrossBorder"].sort());
  assert.equal(snapshot.market.metric, primary.metric);
  assert.equal(snapshot.market.valuation, primary.valuation);
  assert.equal(snapshot.market.scopeKey, "aShareStockEtf");
  assert.equal(snapshot.market.flow1d, primary.scopeTotals.aShareStockEtf.flow1d);
  assert.equal(snapshot.market.etfCount, primary.scopeTotals.aShareStockEtf.etfCount);
  assert.equal(snapshot.market.increaseEtfCount1d + snapshot.market.decreaseEtfCount1d + snapshot.market.unchangedEtfCount1d, snapshot.market.etfCount);

  const assetKeys = ["aShareStockEtf", "crossBorderStockEtf", "bondEtf", "moneyEtf", "commodityEtf", "otherEtf"];
  assert.deepEqual(Object.keys(primary.assetClassTotals).sort(), assetKeys.sort());
  const assetFlow = Object.values(primary.assetClassTotals).reduce((sum, row) => sum + row.flow1d, 0);
  assert.ok(Math.abs(assetFlow - primary.scopeTotals.allEtf.flow1d) <= 0.12);
  assert.equal(primary.assetClassReconciliation.difference, 0);
  assert.ok(Math.abs(primary.assetClassTotals.aShareStockEtf.flow1d + primary.assetClassTotals.crossBorderStockEtf.flow1d - primary.scopeTotals.stockEtfIncludingCrossBorder.flow1d) <= 0.12);

  const secondary = snapshot.flowMetrics.secondaryMarketOrderFlow;
  assert.equal(secondary.metric, "secondaryMarketMainOrderFlow");
  assert.match(secondary.definition, /不是ETF申购赎回/);
  assert.ok(["available", "unavailable"].includes(secondary.status));

  assert.equal(snapshot.universe.length, snapshot.quality.marketEtfCount);
  assert.equal(snapshot.quality.completeUniverseCount, snapshot.quality.marketEtfCount);
  assert.ok(snapshot.universeAudit);
  assert.ok(snapshot.universe.every((row) => "assetScope" in row));
  assert.equal(new Set(snapshot.etfs.map((row) => row.code)).size, snapshot.quality.classifiedEtfCount);
  assert.equal(snapshot.quality.classifiedAshareScopeEnforcement.afterCount, snapshot.etfs.length);
  assert.ok(!Object.hasOwn(snapshot.quality.classifiedAshareScopeEnforcement.excludedByScope, "aShareStockEtf"));
  for (const row of snapshot.etfs) {
    assert.equal(row.assetScope, "aShareStockEtf");
    assert.equal(row.flowMetric, "primaryMarketNetSubscriptionEstimate");
    assert.equal(row.flowValuation, "sameDayUnitNAV");
    assert.equal(typeof row.shareDelta1d, "number");
    assert.equal(typeof row.nav, "number");
  }

  assert.ok(snapshot.groups.some((row) => row.kind === "broad"));
  assert.ok(snapshot.groups.some((row) => row.kind === "style"));
  assert.ok(snapshot.groups.some((row) => row.kind === "industry"));
  const memberCodesByGroup = new Map();
  for (const row of snapshot.etfs) {
    if (!memberCodesByGroup.has(row.groupId)) memberCodesByGroup.set(row.groupId, new Set());
    memberCodesByGroup.get(row.groupId).add(row.code);
  }
  for (const row of snapshot.groups) {
    assert.equal(typeof row.flow1d, "number");
    assert.equal(typeof row.flow5d, "number");
    assert.equal(typeof row.flow20d, "number");
    assert.equal(row.flow5dMetric, "endpointShareChangeTimesCurrentNAV");
    assert.equal(row.flow20dMetric, "endpointShareChangeTimesCurrentNAV");
    assert.ok(memberCodesByGroup.get(row.id)?.has(row.representative.code));
  }

  const marketRecon = snapshot.quality.marketScopeReconciliation;
  const reconMarketFlow = marketRecon.aShareEquityShareFlow1d ?? marketRecon.aShareEquityPrimaryFlow1d;
  const reconClassifiedFlow = marketRecon.classifiedGroupShareFlow1d ?? marketRecon.classifiedGroupPrimaryFlow1d;
  assert.equal(reconMarketFlow, snapshot.market.flow1d);
  assert.ok(Number.isFinite(reconClassifiedFlow));
  assert.ok(Number.isFinite(marketRecon.ungroupedDifference));
  assert.ok(snapshot.quality.classifiedCoverageOfMarketPct >= 95);

  assert.ok(Array.isArray(snapshot.industryRollups));
  assert.ok(snapshot.industryRollups.length > 0 && snapshot.industryRollups.length <= 31);
  assert.ok(snapshot.industryRollups.every((row) => row.kind === "industryRollup"));
  assert.ok(snapshot.industryRollups.some((row) => row.name === "电子"));
  assert.ok(!snapshot.industryRollups.some((row) => row.name === "半导体"));

  const visibleSectors = snapshot.groups.filter((row) => row.kind === "industry");
  const topSectorIn = [...visibleSectors].sort((a, b) => b.flow1d - a.flow1d)[0];
  const topSectorOut = [...visibleSectors].sort((a, b) => a.flow1d - b.flow1d)[0];
  const sectorRecon = snapshot.quality.clientSectorReconciliation;
  assert.equal(sectorRecon.displayLayer, "mutually_exclusive_sw_level_and_theme_groups");
  assert.equal(sectorRecon.topInflowGroup.name, topSectorIn.name);
  assert.equal(sectorRecon.topOutflowGroup.name, topSectorOut.name);
  assert.ok(Math.abs(sectorRecon.difference) <= 0.06);
  assert.ok(Math.abs(sectorRecon.visibleGroupFlow1d - sectorRecon.industryRollupFlow1d) <= 0.06);

  const primaryValue = snapshot.market.flow1d;
  const primaryPattern = primaryValue === 0
    ? /ETF份额对应申赎资金(?:基本持平|净额0\.0亿元)/
    : new RegExp(`ETF份额对应申赎资金(?:小幅|明显|大幅)?净${primaryValue > 0 ? "流入" : "流出"}${escapeRegExp(Math.abs(primaryValue).toFixed(1))}亿元`);
  assert.match(snapshot.conclusion.headline, primaryPattern);
  assert.match(snapshot.conclusion.headline, /\n—— /);
  assert.doesNotMatch(snapshot.conclusion.headline, /A股股票ETF当日合计/);
  assert.doesNotMatch(snapshot.conclusion.headline, /宽基\d+组中/);
  assert.doesNotMatch(snapshot.conclusion.headline, /申万一级和主题行业/);
  assert.equal(snapshot.conclusion.facts.length, 4);
  assert.ok(snapshot.conclusion.facts.every((fact) => !String(fact).includes("份额")));
  const sectorFact = String(snapshot.conclusion.facts[2] || "");
  assert.ok(sectorFact.includes(topSectorIn.name), `facts[2] should name top inflow group ${topSectorIn.name}`);
  assert.ok(sectorFact.includes(topSectorOut.name), `facts[2] should name top outflow group ${topSectorOut.name}`);
  const broadFact = String(snapshot.conclusion.facts[0] || "");
  const broadGroups = snapshot.groups.filter((g) => g.kind === "broad");
  const broadIn = broadGroups.filter((g) => Number(g.flow1d || 0) > 0).length;
  const broadOut = broadGroups.filter((g) => Number(g.flow1d || 0) < 0).length;
  assert.ok(broadFact.includes(`共${broadGroups.length}组，${broadOut}个净流出、${broadIn}个净流入`), `facts[0] broad counts mismatch: ${broadFact}`);
  assert.doesNotMatch(snapshot.conclusion.headline, /申万一级行业资金流入居前/);

  const trade = snapshot.flowMetrics.secondaryMarketTradeFlow;
  if (trade.status === "available") {
    assert.equal(trade.tradeDate, snapshot.tradeDate);
    const tradeValue = trade.scopeTotals.aShareStockEtf.netFlow1d;
    const tradePattern = new RegExp(
      `^A股ETF盘中(?:买卖力量基本均衡|(?:买|卖)盘(?:小幅偏强|偏强|明显占优)，主动${tradeValue > 0 ? "买入" : "卖出"}净额${escapeRegExp(Math.abs(tradeValue).toFixed(1))}亿元)；`
    );
    assert.match(snapshot.conclusion.headline, tradePattern);
  } else {
    assert.ok(snapshot.conclusion.headline.startsWith("A股ETF盘中主动买卖数据暂缺；"));
  }

  assert.match(snapshot.methodology.identity, /禁止据此推断/);
  assert.match(snapshot.methodology.flow, /T日单位净值/);
  assert.match(snapshot.methodology.metricSeparation, /主动买卖净额/);
  assert.match(snapshot.methodology.metricSeparation, /ETF份额对应申赎资金/);
  assert.match(snapshot.methodology.sectorDisplay, /申万一级行业\+热门主题/);
  assert.match(snapshot.methodology.multiDay, /不是逐日净(?:申购|流入)额之和/);
  assert.match(snapshot.methodology.scope, /A股股票ETF/);
  assert.match(snapshot.methodology.scope, /股票ETF（含跨境）/);

  const daily = JSON.parse(await readFile(`site/data/daily/${snapshot.tradeDate}.json`, "utf8"));
  assert.equal(daily.metric, primary.metric);
  assert.equal(daily.valuation, primary.valuation);
  assert.equal(daily.tradeDate, snapshot.tradeDate);
  assert.equal(daily.marketScopes.aShareStockEtf.flow1d, snapshot.market.flow1d);
});

test("generated client uses endpoint labels and the same industry/theme terminology as the headline", async () => {
  const page = await readFile("dist/index.html", "utf8");
  const snapshot = JSON.parse(await readFile("dist/data/latest.json", "utf8"));
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /A股股票ETF一级市场净申赎/);
  assert.match(page, /5日端点资金变化/);
  assert.match(page, /20日端点资金变化/);
  assert.match(page, /申万一级和主题行业资金坐标/);
  assert.doesNotMatch(page, /5日累计资金变化/);
  assert.doesNotMatch(page, /20日累计资金变化/);
  assert.doesNotMatch(page, /全部场内ETF · 一级市场/);
  assert.doesNotMatch(page, /股票ETF（含跨境）· 一级市场/);
  assert.doesNotMatch(page, /A股股票ETF · 二级市场主力资金/);
  assert.equal(snapshot.schemaVersion, 6);
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
