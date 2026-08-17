import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";

const out = new URL("../dist/", import.meta.url);
let page = await readFile(new URL("../site/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../site/styles.css", import.meta.url), "utf8");
const htmlToImage = await readFile(new URL("../node_modules/html-to-image/dist/html-to-image.js", import.meta.url), "utf8");

const textReplacements = [
  ["A股股票ETF当日资金变化", "A股股票ETF一级市场净申赎"],
  ["5日累计资金变化", "5日端点资金变化"],
  ["20日累计资金变化", "20日端点资金变化"],
  ["5日累计", "5日端点变化"],
  ["1日资金变化", "1日净申赎估算"],
  ["近5日资金方向", "5日端点资金方向"],
  ["近5日净流入", "5日端点净流入"],
  ["近5日净流出", "5日端点净流出"],
  ["资金为组内ETF近5日净流入/流出", "资金为组内ETF的5日端点份额变化估算"],
  ["先看今天，再用5日和20日辨别一次性申赎还是持续切换", "1日看一级市场净申赎，5日和20日为端点份额变化，用于观察持续性"],
  ["参考规模 = 日终总份额 × 当日成交均价（成交额÷成交量），资金变化 = 份额变化 × 当日成交均价；成交均价缺失时依次回退当日收盘价、单位净值。", "参考规模 = 日终份额 × 同日单位净值；1日主口径 = 公司行动调整后的份额变化 × 同日单位净值。成交均价只作为Wind类口径对照估算，不与主口径混用。"],
  ["北京时间0:15、0:30、1:00、5:00、6:00、7:00和8:00", "北京时间5:00、6:00、7:00和8:00"],
  ["北京时间0:15、0:30、1:00、5:00、6:00、7:00、8:00", "北京时间5:00、6:00、7:00、8:00"],
];
for (const [from, to] of textReplacements) page = page.replaceAll(from, to);

const metricHelper = [
  "",
  "  function metricScopeStrip(data){",
  "    const pm=data.flowMetrics?.primaryMarket||{},primary=pm.scopeTotals||{},secondary=data.flowMetrics?.secondaryMarketOrderFlow||{};",
  "    const avg=pm.valuationComparisons?.alternatives?.sameDayAverageTradedPrice?.scopeTotals||{};",
  "    if(!Object.keys(primary).length)return \"\";",
  '    const card=(key,title,note)=>{const row=primary[key],alt=avg[key];return `<article><span>${title}</span><strong class="${tone(row?.flow1d)}">${money(row?.flow1d)}</strong><small>${note}${row?.etfCount!=null?` · ${row.etfCount}只`:""}${alt?.flow1d!=null?` · 成交均价对照 ${money(alt.flow1d)}`:""}</small></article>`};',
  "    const secondaryRow=secondary.status===\"available\"?secondary.scopeTotals?.aShareStockEtf:null;",
  "    const secondaryText=secondary.status===\"available\"?money(secondaryRow?.flow1d):\"未留存同日快照\";",
  '    return `<section class="market-strip metric-scope-strip">${card("allEtf","全部场内ETF · 一级市场","NAV主口径")}${card("stockEtfIncludingCrossBorder","股票ETF（含跨境）· 一级市场","NAV主口径")}${card("aShareStockEtf","A股股票ETF · 一级市场","本站主研究口径") }<article><span>A股股票ETF · 二级市场主力资金</span><strong class="${secondary.status==="available"?tone(secondaryRow?.flow1d):"flat"}">${secondaryText}</strong><small>${secondary.status==="available"?"成交订单流，不等于申购赎回":`未留存${secondary.tradeDate||"该交易日"}同日订单流，未强行回填`}</small></article></section>`;',
  "  }",
  "",
].join("\n");
page = page.replace("\n  function render(data){", `${metricHelper}\n  function render(data){`);
page = page.replace(
  "\n      <section id=\"broad\" class=\"panel\">",
  "\n      ${metricScopeStrip(data)}\n\n      <section id=\"broad\" class=\"panel\">",
);

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await writeFile(new URL("index.html", out), page, "utf8");
await writeFile(new URL("styles.css", out), css, "utf8");
await writeFile(new URL("html-to-image.js", out), htmlToImage, "utf8");
await cp(new URL("../site/data/", import.meta.url), new URL("data/", out), { recursive: true });
await cp(new URL("../site/favicon.svg", import.meta.url), new URL("favicon.svg", out));
await writeFile(new URL(".nojekyll", out), "", "utf8");

console.log(`Static site bundle created at ${out.pathname}`);
