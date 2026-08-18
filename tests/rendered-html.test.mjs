import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const forbiddenClientPhrases = [
  "5日累计资金变化",
  "20日累计资金变化",
  "当日成交资金净流入/净流出",
  "申万行业口径",
  "申万行业分类标准2021版",
  "资金为组内ETF近5日净流入/流出",
];

const assertPreciseClientSource = (page) => {
  assert.match(page, /A股股票ETF当日一级市场净申赎估算/);
  assert.match(page, /5日逐日累计净申赎估算/);
  assert.match(page, /20日逐日累计净申赎估算/);
  assert.match(page, /仅在5个已验证交易日事实齐全时发布/);
  assert.match(page, /代表ETF价格 × 端点份额变化/);
  assert.match(page, /该图不是5日逐日累计资金流/);
  assert.match(page, /研究分组口径/);
  assert.match(page, /歧义ETF保留在市场总量，但不进入研究分组结论/);
  assert.match(page, /二级市场成交方向统计单独保存，不与一级市场申赎混用/);
  assert.match(page, /data\.dataContractVersion!=="7\.0"/);
  for (const phrase of forbiddenClientPhrases) assert.ok(!page.includes(phrase), `legacy phrase remains: ${phrase}`);
};

test("dashboard source has one precise financial wording contract", async () => {
  const page = await readFile("site/index.html", "utf8");
  const build = await readFile("scripts/build-site.mjs", "utf8");
  const css = await readFile("site/styles.css", "utf8");

  assert.match(page, /资金ETF流动每日跟踪/);
  assert.match(page, /主要宽基数据摘要/);
  assert.match(page, /全部研究组证据表/);
  assert.match(page, /数据、口径与溯源/);
  assert.match(page, /导出高清 JPG/);
  assert.match(page, /全量ETF每日变更检查/);
  assertPreciseClientSource(page);

  assert.ok(!build.includes("textReplacements"));
  assert.ok(!build.includes("replaceAll(from, to)"));
  assert.match(build, /source page is the only wording contract/i);
  assert.match(css, /\.e-row\[hidden\]\{display:none!important\}/);
});

test("built client is byte-semantic equivalent to source, not a wording rewrite", async () => {
  const source = await readFile("site/index.html", "utf8");
  const built = await readFile("dist/index.html", "utf8");
  assert.equal(built, source);
  assertPreciseClientSource(built);
});

test("persisted snapshot retains the validated primary-market base contract", async () => {
  const snapshot = JSON.parse(await readFile("site/data/latest.json", "utf8"));
  assert.equal(snapshot.sourceMode, "REAL");
  assert.ok(["verified", "warning"].includes(snapshot.status));
  assert.ok(snapshot.quality.officialSessions >= 21);

  const primary = snapshot.flowMetrics.primaryMarket;
  assert.equal(primary.metric, "primaryMarketNetSubscriptionEstimate");
  assert.equal(primary.valuation, "sameDayUnitNAV");
  assert.equal(snapshot.market.flow1d, primary.scopeTotals.aShareStockEtf.flow1d);
  assert.equal(snapshot.market.etfCount, primary.scopeTotals.aShareStockEtf.etfCount);

  const assetKeys = ["aShareStockEtf", "crossBorderStockEtf", "bondEtf", "moneyEtf", "commodityEtf", "otherEtf"];
  assert.deepEqual(Object.keys(primary.assetClassTotals).sort(), assetKeys.sort());
  const assetFlow = Object.values(primary.assetClassTotals).reduce((sum, row) => sum + row.flow1d, 0);
  assert.ok(Math.abs(assetFlow - primary.scopeTotals.allEtf.flow1d) <= 0.12);
});

test("a v7 snapshot, when present, satisfies the unified client semantics", async () => {
  const snapshot = JSON.parse(await readFile("site/data/latest.json", "utf8"));
  if (snapshot.dataContractVersion !== "7.0") return;

  assert.equal(snapshot.quality.dataContractVersion, "7.0");
  const primary = snapshot.flowMetrics.primaryMarket;
  assert.match(primary.displayName, /一级市场/);
  assert.match(primary.definition, /T日单位净值/);

  const market = snapshot.market;
  assert.equal(
    market.increaseEtfCount1d + market.decreaseEtfCount1d + market.unchangedEtfCount1d,
    market.etfCount,
  );
  const asset = primary.assetClassTotals.aShareStockEtf;
  assert.deepEqual(
    [asset.increaseEtfCount1d, asset.decreaseEtfCount1d, asset.unchangedEtfCount1d],
    [market.increaseEtfCount1d, market.decreaseEtfCount1d, market.unchangedEtfCount1d],
  );

  for (const horizon of [5, 20]) {
    const status = market[`flow${horizon}dCumulativeStatus`];
    if (status === "available") {
      assert.equal(market[`flow${horizon}d`], market[`flow${horizon}dCumulative`]);
    } else {
      assert.equal(market[`flow${horizon}d`], null);
      assert.equal(market[`flow${horizon}dCumulative`], null);
    }
    assert.equal(typeof market[`flow${horizon}dEndpoint`], "number");
  }

  const ambiguous = new Set(snapshot.universe.filter((row) => row.classificationStatus === "ambiguous").map((row) => row.code));
  assert.ok(snapshot.etfs.every((row) => !ambiguous.has(row.code)));
  assert.ok(snapshot.etfs.every((row) => row.assetScope === "aShareStockEtf"));
  assert.ok(snapshot.groups.every((row) => row.classificationClaim === "研究分组，不代表基金管理人或指数公司官方分类"));

  const known = snapshot.universe.find((row) => row.code === "510150");
  if (known?.groupId === "sw_food_beverage") assert.equal(known.classificationStatus, "ambiguous");

  const trade = snapshot.flowMetrics.secondaryMarketTradeFlow;
  if (trade) {
    assert.equal(trade.metric, "secondaryMarketAggressorImbalanceEstimate");
    assert.match(trade.definition, /不代表市场净新增资金/);
    assert.match(trade.definition, /不是ETF一级市场申购赎回/);
  }

  assert.match(snapshot.conclusion.headline, /交易所日终份额变化/);
  assert.match(snapshot.conclusion.headline, /估算/);
  assert.match(snapshot.conclusion.interpretation, /不等同于二级市场成交资金/);
  assert.ok(!snapshot.conclusion.headline.includes("当日成交资金净"));
  assert.ok(!snapshot.conclusion.headline.includes("申万一级和主题行业"));

  assert.match(snapshot.methodology.multiDay, /逐日累计净申赎/);
  assert.match(snapshot.methodology.classification, /ambiguous/);
  assert.match(snapshot.methodology.coordinates, /代表ETF/);
  assert.match(snapshot.methodology.secondary, /不是ETF一级市场净申购\/赎回/);
  assert.ok(snapshot.provenance.primaryShares);
  assert.ok(snapshot.provenance.navAndFundType);
});

test("deployment blueprint builds the exact source client", async () => {
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
