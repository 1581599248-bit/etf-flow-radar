import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("dashboard contains the answer-first modules and two coordinate maps", async () => {
  const page = await readFile("site/index.html", "utf8");
  const css = await readFile("site/styles.css", "utf8");
  assert.match(page, /资金ETF流动每日跟踪/);
  assert.match(page, /主要宽基数据摘要/);
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /申万一级与主流行业资金坐标/);
  assert.match(page, /申万行业分类标准2021版/);
  assert.match(page, /20日相对沪深300收益/);
  assert.match(page, /5日资金变化率（占5日前规模）/);
  assert.match(page, /当日ETF流入流出分布/);
  assert.match(page, /\$\{r\[`increaseEtfCount\$\{period\}`\].*\}只流入 \/ \$\{r\[`decreaseEtfCount\$\{period\}`\].*\}只流出/);
  assert.match(page, /数据解读/);
  assert.match(page, /ETF跟踪观点/);
  assert.match(page, /下一交易日盘前使用/);
  assert.doesNotMatch(page, /我们怎么看|识别边界| bp|观察池当日估算资金变化|已通过门禁|滚动\$\{data\.quality\.officialSessions\}/);
  assert.match(page, /导出高清 JPG/);
  assert.match(page, /全量ETF每日变更检查/);
  assert.match(page, /交易所完整ETF/);
  assert.match(page, /气泡面积按组内ETF参考规模线性编码/);
  assert.match(page, /组内ETF参考规模/);
  assert.match(page, /layoutBubbleLabels/);
  assert.match(page, /全部标签就近避让/);
  assert.match(page, /全部资金观察组证据表/);
  assert.match(page, /不再设置跨行业主题组/);
  assert.doesNotMatch(page, /data-kind="theme"/);
  assert.match(page, /等待历史或净值补齐/);
  assert.doesNotMatch(page, /仅显示防碰撞标签/);
  assert.match(page, /当日流入领跑 \/ 流出领跑/);
  assert.match(page, /近5日.*流入领跑 \/ 流出领跑/);
  assert.doesNotMatch(page, /气泡面积 = 当前估算规模|当日流入 \/ 流出领跑/);
  assert.doesNotMatch(page, /国家队代理ETF净流入|代理池当日估算净流入/);
  assert.doesNotMatch(page, /总份额不变|只不变|不变\$\{|当日估算|估算净流入|估算净流出/);
  assert.doesNotMatch(page, /上涨增配|上涨减配|逆势承接|下跌流出|下跌但流入|已完成分析的A股股票ETF/);
  assert.match(css, /\.e-row\[hidden\]\{display:none!important\}/);
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
  assert.ok(snapshot.groups.some((row) => row.kind === "industry"));
  assert.ok(snapshot.groups.every((row) => row.kind !== "theme"));
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
  assert.equal(snapshot.market.increaseEtfCount1d + snapshot.market.decreaseEtfCount1d + snapshot.market.unchangedEtfCount1d, snapshot.market.etfCount);
  assert.equal(snapshot.quality.reconciliation.directionCountTotal, snapshot.market.etfCount);
  assert.equal(snapshot.quality.reconciliation.groupEtfCountTotal, snapshot.market.etfCount);
  assert.equal(snapshot.quality.reconciliation.uniqueAnalyzedEtfCount, snapshot.market.etfCount);
  assert.ok(Math.abs(snapshot.quality.reconciliation.flowDifference) <= 0.5);
  assert.equal(new Set(snapshot.etfs.map((row) => row.code)).size, snapshot.market.etfCount);
  assert.match(snapshot.methodology.counts, /不代表没有二级市场成交/);
  const allowedStates = new Set(["跑赢且流入", "跑输但流入", "跑赢但流出", "跑输且流出"]);
  assert.ok(snapshot.groups.every((row) => allowedStates.has(row.priceFlowState)));
  for (const row of snapshot.groups) {
    const expected = row.relativeReturn20d >= 0
      ? (row.flowIntensity5dPct >= 0 ? "跑赢且流入" : "跑赢但流出")
      : (row.flowIntensity5dPct >= 0 ? "跑输但流入" : "跑输且流出");
    assert.equal(row.priceFlowState, expected);
  }
  const industryGroups = snapshot.groups.filter((row) => row.kind === "industry");
  const industryParentCount = new Set(industryGroups.map((row) => row.parent || row.id)).size;
  assert.ok(industryParentCount <= 31);
  assert.equal(snapshot.quality.industryDefinitionCount, 31);
  assert.equal(snapshot.quality.industryGroupCount, industryParentCount);
  assert.equal(snapshot.quality.industryGroupCount + snapshot.quality.industryMissingGroups.length, 31);
  assert.equal(snapshot.quality.industryEtfCount, industryGroups.reduce((sum, row) => sum + row.etfCount, 0));
  const universeIndustryRows = snapshot.universe.filter((row) => row.classificationStatus === "classified" && row.kind === "industry");
  assert.equal(snapshot.quality.industryUniverseCount, universeIndustryRows.length);
  assert.equal(snapshot.quality.industryPendingCount, universeIndustryRows.length - snapshot.quality.industryEtfCount);
  assert.match(snapshot.methodology.classification, /SW2021_L1_ETF_V2/);
  assert.match(snapshot.methodology.classification, /31个一级行业/);
});

test("generated output and Render blueprint share one reproducible directory", async () => {
  const page = await readFile("dist/index.html", "utf8");
  const snapshot = JSON.parse(await readFile("dist/data/latest.json", "utf8"));
  const blueprint = await readFile("render.yaml", "utf8");
  assert.match(page, /宽基与风格资金坐标/);
  assert.match(page, /申万一级与主流行业资金坐标/);
  assert.ok(snapshot.schemaVersion >= 4);
  assert.match(blueprint, /buildCommand: npm ci && npm run build/);
  assert.match(blueprint, /staticPublishPath: \.\/dist/);
  assert.match(blueprint, /autoDeployTrigger: commit/);
});
