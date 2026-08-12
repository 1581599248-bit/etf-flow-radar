"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type DominantEtf = { code: string; name: string; flow1d: number; grossContributionPct: number };
type IndexRow = {
  code: string; name: string; group: string; flow1d: number; flow5d: number | null; flow20d: number | null;
  shareChangePct: number; etfCount: number; aum: number; flowIntensityBps: number; breadthScore: number;
  inflowEtfCount: number; outflowEtfCount: number; unchangedEtfCount: number; dominantEtf: DominantEtf;
  percentile: number | null; status: string; spark: number[];
};
type EtfRow = {
  code: string; name: string; exchange: string; indexCode: string; indexName: string; group: string;
  shares: number; previousShares: number; shareChangePct: number; referencePrice: number;
  referencePriceType: "NAV" | "CLOSE"; estimatedFlow: number; source: string;
};
type Snapshot = {
  schemaVersion: number; status: "verified" | "warning" | "failed"; tradeDate: string; previousTradeDate: string;
  generatedAt: string; sourceMode: "REAL"; quality: { marketEtfCount: number; priceCoverage: number;
  previousShareCoverage: number; shareReconciliationRate: number | null; mappedEtfCount: number;
  issues: { severity: string; check: string; message: string }[] }; sources: { name: string; field: string; role: string }[];
  indices: IndexRow[]; etfs: EtfRow[]; methodology: string;
};

const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const money = (n: number | null) => n === null ? "—" : `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(2)} 亿`;
const tone = (n: number) => n > 0 ? "up" : n < 0 ? "down" : "flat";
const fullDate = (iso: string) => new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date(`${iso}T12:00:00+08:00`));
const signed = (n: number, digits = 1) => `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(digits)}`;

function Stat({ label, value, note, valueTone = "flat" }: { label: string; value: string; note: string; valueTone?: string }) {
  return <article className="stat"><span>{label}</span><strong className={valueTone}>{value}</strong><small>{note}</small></article>;
}

function FlowBar({ value, max }: { value: number; max: number }) {
  const width = Math.max(2, Math.abs(value) / Math.max(max, 0.01) * 48);
  return <div className="flowbar"><span className={tone(value)} style={value >= 0 ? { left: "50%", width: `${width}%` } : { right: "50%", width: `${width}%` }} /><i /></div>;
}

function Quadrant({ rows }: { rows: IndexRow[] }) {
  const limit = Math.max(...rows.map(row => Math.abs(row.flowIntensityBps)), 100) * 1.12;
  return <div className="quadrant-wrap">
    <div className="quadrant" role="img" aria-label="横轴为净申赎强度，纵轴为ETF申赎广度的四象限散点图">
      <span className="q-label q-tl">少数大额流出</span><span className="q-label q-tr">广泛流入</span>
      <span className="q-label q-bl">广泛流出</span><span className="q-label q-br">少数大额流入</span>
      <i className="q-axis q-axis-x" /><i className="q-axis q-axis-y" />
      {rows.map(row => {
        const left = 50 + row.flowIntensityBps / limit * 45;
        const top = 50 - Math.max(-60, Math.min(60, row.breadthScore)) / 60 * 43;
        const size = 8 + Math.min(8, Math.log10(Math.max(row.aum, 10)) * 2);
        return <button key={row.code} className={`q-point ${tone(row.flow1d)}`} style={{ left: `${left}%`, top: `${top}%`, width: size, height: size }} title={`${row.name}｜强度 ${signed(row.flowIntensityBps)}bp｜广度 ${signed(row.breadthScore)}%`}><span>{row.name}</span></button>;
      })}
      <span className="axis-name axis-x">净申赎强度（bp / 估算AUM）→</span><span className="axis-name axis-y">ETF申赎广度 →</span>
    </div>
    <div className="chart-legend"><span><i className="dot up" />聚合净流入</span><span><i className="dot down" />聚合净流出</span><span>气泡大小：估算AUM</span></div>
  </div>;
}

export default function Home() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"flow1d" | "flowIntensityBps" | "breadthScore">("flow1d");
  const [selected, setSelected] = useState<IndexRow | null>(null);
  const [exporting, setExporting] = useState(false);
  const reportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      let lastError: unknown;
      for (const url of ["/api/snapshot", "/data/latest.json"]) {
        try {
          const response = await fetch(url, { cache: "no-store" });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const snapshot = await response.json() as Snapshot;
          if (snapshot.schemaVersion < 3 || snapshot.sourceMode !== "REAL" || snapshot.status === "failed") throw new Error("当前快照未通过新版研究口径");
          setData(snapshot); return;
        } catch (caught) { lastError = caught; }
      }
      setError(String(lastError instanceof Error ? lastError.message : lastError));
    })();
  }, []);

  const indices = useMemo(() => !data ? [] : [...data.indices]
    .filter(row => row.name.includes(query) || row.code.includes(query))
    .sort((a, b) => b[sort] - a[sort]), [data, query, sort]);

  const exportJpg = async () => {
    const node = reportRef.current; if (!node || !data) return;
    setExporting(true); node.classList.add("exporting");
    try {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const { toJpeg } = await import("html-to-image");
      const url = await toJpeg(node, { quality: .98, pixelRatio: 2, backgroundColor: "#f1f2f4", cacheBust: true,
        filter: element => !(element instanceof HTMLElement && element.dataset.exportHide === "true") });
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `资金ETF流动每日跟踪_${data.tradeDate}.jpg`; anchor.click();
    } catch (caught) { alert(`导出失败：${String(caught)}`); }
    finally { node.classList.remove("exporting"); setExporting(false); }
  };

  if (error) return <main className="blocked"><b>数据未发布</b><h1>没有通过新版研究口径的真实快照</h1><p>{error}</p><small>系统不会回退到模拟数据或旧口径结论。</small></main>;
  if (!data) return <main className="blocked"><b>REAL DATA</b><h1>正在读取已验证快照…</h1></main>;

  const rows = data.indices;
  const total = rows.reduce((sum, row) => sum + row.flow1d, 0);
  const gross = rows.reduce((sum, row) => sum + Math.abs(row.flow1d), 0);
  const poolAum = rows.reduce((sum, row) => sum + row.aum, 0);
  const inflow = rows.filter(row => row.flow1d > 0).length;
  const outflow = rows.filter(row => row.flow1d < 0).length;
  const positiveEtfs = rows.reduce((sum, row) => sum + row.inflowEtfCount, 0);
  const negativeEtfs = rows.reduce((sum, row) => sum + row.outflowEtfCount, 0);
  const totalEtfs = rows.reduce((sum, row) => sum + row.etfCount, 0);
  const breadth = (positiveEtfs - negativeEtfs) / totalEtfs * 100;
  const topOut = [...rows].sort((a, b) => a.flow1d - b.flow1d);
  const pressureShare = (Math.abs(topOut[0].flow1d) + Math.abs(topOut[1].flow1d)) / gross * 100;
  const largeRows = rows.filter(row => ["000016", "000300", "000510"].includes(row.code));
  const historyDays = Math.max(...rows.map(row => row.spark.length));
  const maxFlow = Math.max(...indices.map(row => Math.abs(row.flow1d)), 1);

  return <div className="site">
    <nav className="nav"><div className="mark">E</div><div className="nav-title"><strong>资金ETF流动每日跟踪</strong><span>ETF FLOW RESEARCH</span></div><div className="nav-links"><a href="#thesis">结论</a><a href="#coordinate">坐标</a><a href="#indices">明细</a><a href="#quality">口径</a></div><button onClick={exportJpg} disabled={exporting} data-export-hide="true">{exporting ? "正在生成…" : "导出高清 JPG"}</button></nav>
    <div className="report" ref={reportRef}>
      <header className="report-head"><div><p>A-SHARE ETF CAPITAL FLOW · EVIDENCE FIRST</p><h1>资金ETF流动每日跟踪</h1><span className="head-scope">核心指数ETF观察池 · 非全市场资金流 · 非投资主体识别</span></div><div className="analysis-date"><span>每日分析日期</span><strong>{fullDate(data.tradeDate)}</strong><small>生成于 {new Date(data.generatedAt).toLocaleString("zh-CN", { hour12: false })}</small></div></header>

      <section className="trust-strip"><b><i />真实数据已验证</b><span>全市场份额 {number.format(data.quality.marketEtfCount)} 只</span><span>纯基准映射 {data.quality.mappedEtfCount} 只</span><span>同日价格覆盖 {(data.quality.priceCoverage * 100).toFixed(2)}%</span><span>T−1 {data.previousTradeDate}</span></section>

      <section className="thesis" id="thesis">
        <div className="thesis-main"><div className="eyebrow"><span>今日结论</span><b>C级｜单日观察，趋势未确认</b></div><h2>压力集中在成长与中小盘，大盘内部明显分化，不能概括为“资金偏向大盘”。</h2><p>9 个核心指数ETF观察池合计估算净流出 <strong className={tone(total)}>{money(total)}</strong>；{topOut[0].name}与{topOut[1].name}流出居前。{largeRows.map(row => `${row.name} ${money(row.flow1d)}`).join("、")}，方向并不一致。</p></div>
        <div className="evidence-grid">
          <article><span>发生了什么</span><b>{outflow} / {rows.length}</b><p>指数方向净流出；观察池申赎广度 {signed(breadth)}%</p></article>
          <article><span>压力在哪里</span><b>{topOut[0].name} · {topOut[1].name}</b><p>两者占观察池绝对资金变化 {pressureShare.toFixed(1)}%</p></article>
          <article><span>关键反证</span><b>大盘不是同向信号</b><p>上证50、A500流入，但沪深300流出</p></article>
          <article><span>证据强度</span><b>仅 {historyDays} 个交易日</b><p>5日、20日与250日位置均不可判断</p></article>
        </div>
        <div className="decision-line"><span><b>可以确认</b> 当日哪些观察池发生份额增减</span><span><b>不能确认</b> 国家队或任何投资者身份、持续趋势、未来收益</span><span><b>下一步确认</b> 连续5日方向、申赎广度扩散、集中度是否下降</span></div>
      </section>

      <section className="stats-grid">
        <Stat label="观察池合计净流" value={money(total)} note="指数暴露重叠，不等于全市场净流" valueTone={tone(total)} />
        <Stat label="绝对资金变化" value={`${gross.toFixed(2)} 亿`} note="流入与流出绝对值之和" />
        <Stat label="观察池估算AUM" value={`${number.format(poolAum)} 亿`} note="T日份额 × T日单位净值" />
        <Stat label="指数方向分布" value={`${inflow} 入 / ${outflow} 出`} note={`${rows.length} 个核心指数`} />
        <Stat label="产品申赎广度" value={`${signed(breadth)}%`} note={`${positiveEtfs}只增 / ${negativeEtfs}只减 / ${totalEtfs}只`} valueTone={tone(breadth)} />
        <Stat label="历史可用性" value={`${historyDays} 日`} note="不足则不输出趋势与分位" />
      </section>

      <section className="analysis-grid" id="coordinate">
        <article className="panel coordinate-panel"><div className="panel-head"><div><span>FLOW COORDINATE</span><h2>资金强度 × 申赎广度</h2><p>横轴衡量相对规模，纵轴检验资金变化是否由多数ETF共同确认</p></div><b className="scope-tag">截面 · {data.tradeDate}</b></div><Quadrant rows={rows} /><p className="method-note">强度 = 估算净申赎 ÷ T日估算AUM；广度 =（份额增加ETF数 − 份额减少ETF数）÷ ETF总数。右下象限表示合计流入但多数产品未同步，并非广泛共识。</p></article>
        <aside className="insight-stack">
          <article className="insight-card"><span>最重要的分化</span><h3>中证A500：净流入，但广度为负</h3><p>合计 {money(rows.find(row => row.code === "000510")?.flow1d ?? 0)}，广度 {signed(rows.find(row => row.code === "000510")?.breadthScore ?? 0)}%。说明少数较大申购覆盖了更多产品的小额赎回，应标注为“集中流入”。</p></article>
          <article className="insight-card"><span>最需要降权的信号</span><h3>上证50：单一产品驱动</h3><p>{rows.find(row => row.code === "000016")?.dominantEtf.name}贡献该组绝对变化的 {rows.find(row => row.code === "000016")?.dominantEtf.grossContributionPct.toFixed(0)}%，不能外推为整个大盘风格共识。</p></article>
          <article className="insight-card boundary"><span>识别边界</span><h3>删除“国家队代理资金”</h3><p>交易所ETF份额只告诉我们总份额变化，无法区分中央汇金、机构、做市商或个人。没有投资者身份数据，就不能给资金贴“国家队”标签。</p></article>
        </aside>
      </section>

      <section className="panel" id="indices"><div className="panel-head"><div><span>INDEX EVIDENCE TABLE</span><h2>核心指数证据表</h2><p>同时查看金额、规模化强度、广度和单一产品贡献，避免只看一个总数</p></div><div className="filters" data-export-hide="true"><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索指数 / 代码" /><select value={sort} onChange={event => setSort(event.target.value as typeof sort)}><option value="flow1d">按净流排序</option><option value="flowIntensityBps">按强度排序</option><option value="breadthScore">按广度排序</option></select></div></div>
        <div className="flow-table"><div className="flow-tr flow-th"><span>指数 / 代码</span><span>资金方向</span><span>1日净流</span><span>强度 bp</span><span>申赎广度</span><span>产品分布</span><span>主导ETF / 贡献</span><span>5日</span></div>{indices.map(row => <button className="flow-tr" key={row.code} onClick={() => setSelected(row)}><span><b>{row.name}</b><small>{row.code} · {row.group}</small></span><FlowBar value={row.flow1d} max={maxFlow} /><strong className={tone(row.flow1d)}>{money(row.flow1d)}</strong><span className={tone(row.flowIntensityBps)}>{signed(row.flowIntensityBps)} bp</span><span className={tone(row.breadthScore)}>{signed(row.breadthScore)}%</span><span>{row.inflowEtfCount}增 / {row.outflowEtfCount}减 / {row.unchangedEtfCount}平</span><span><b>{row.dominantEtf.name}</b><small>{row.dominantEtf.grossContributionPct.toFixed(1)}%</small></span><em>{row.flow5d === null ? "样本积累中" : money(row.flow5d)}</em></button>)}</div>
        <p className="method-note">本表仅纳入名称可明确识别为对应头条指数的纯基准ETF；增强、指增、价值、成长、红利、低波、等权等策略变体已排除。点击任一指数查看ETF级贡献。</p></section>

      <section className="lower-grid" id="quality">
        <article className="panel"><div className="panel-head"><div><span>CONFIRMATION CHECKLIST</span><h2>从“单日现象”升级为“轮动结论”还缺什么</h2></div></div><ol className="checklist"><li><b>持续性</b><span>至少5个交易日累计同向，当前：不可用</span></li><li><b>广度扩散</b><span>同组多数ETF份额同向，当前：仅部分指数满足</span></li><li><b>集中度</b><span>信号不能长期由单只ETF主导，当前：上证50、创业板需降权</span></li><li><b>价格验证</b><span>价格与份额联合判断逆势/趋势流入，当前历史收益字段未接入</span></li></ol></article>
        <article className="panel"><div className="panel-head"><div><span>DATA QUALITY</span><h2>真实数据与发布门禁</h2></div><b className="verified">✓ {data.status.toUpperCase()}</b></div><div className="quality-grid"><span><b>份额主源</b>SSE / SZSE 日终总份额</span><span><b>价格口径</b>T日单位净值优先</span><span><b>日期锁定</b>{data.previousTradeDate} → {data.tradeDate}</span><span><b>失败策略</b>不覆盖上一验证快照</span></div><p className="quality-note">{data.quality.issues.map(issue => issue.message).join("；") || "全部质量门禁通过"}</p></article>
      </section>

      <footer><b>资金ETF流动每日跟踪</b><p>数据来源：上海证券交易所、深圳证券交易所、东方财富公开数据；AKShare 为固定版本采集适配层。估算资金流 =（T日总份额 − T−1日总份额）× T日单位净值。本页展示的是重叠指数观察池，不是全市场净流，也不能识别投资者身份。本报告不构成投资建议。</p><span>分析日期 {data.tradeDate}</span></footer>
    </div>

    {selected && <div className="drawer-bg"><button aria-label="关闭详情" onClick={() => setSelected(null)} /><aside className="drawer"><button className="drawer-x" onClick={() => setSelected(null)}>×</button><span>INDEX DETAIL · {selected.code}</span><h2>{selected.name}</h2><div className="drawer-stats"><Stat label="当日估算净流" value={money(selected.flow1d)} note={`${selected.etfCount}只纯基准ETF`} valueTone={tone(selected.flow1d)} /><Stat label="强度 / 广度" value={`${signed(selected.flowIntensityBps)}bp / ${signed(selected.breadthScore)}%`} note={`${selected.inflowEtfCount}增 · ${selected.outflowEtfCount}减 · ${selected.unchangedEtfCount}平`} /></div><h3>ETF贡献明细</h3><div className="etf-list">{data.etfs.filter(etf => etf.indexCode === selected.code).sort((a, b) => Math.abs(b.estimatedFlow) - Math.abs(a.estimatedFlow)).map(etf => <div key={etf.code}><span><b>{etf.name}</b><small>{etf.code} · {etf.exchange} · {etf.referencePriceType}</small></span><span>份额 {number.format(etf.shares / 1e8)}亿</span><span>{signed(etf.shareChangePct, 3)}%</span><strong className={tone(etf.estimatedFlow)}>{money(etf.estimatedFlow / 1e8)}</strong></div>)}</div><p>产品明细与指数头条使用同一过滤口径，金额为估算值。</p></aside></div>}
  </div>;
}
