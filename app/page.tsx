"use client";

import { useMemo, useState } from "react";

type IndexRow = {
  code: string; name: string; flow1d: number; flow5d: number; flow20d: number;
  share: number; percentile: number; status: string; spark: number[];
};

const INDEX_DATA: IndexRow[] = [
  { code: "000300", name: "沪深300", flow1d: 32.8, flow5d: 78.4, flow20d: 126.5, share: 0.42, percentile: 86.4, status: "流入偏强", spark: [22,28,24,35,31,42,38,47,52,49,58,64] },
  { code: "000016", name: "上证50", flow1d: 18.6, flow5d: 43.7, flow20d: 65.9, share: 0.31, percentile: 78.2, status: "流入增强", spark: [20,18,24,21,28,31,35,33,41,44,48,53] },
  { code: "000905", name: "中证500", flow1d: -8.4, flow5d: -21.5, flow20d: 13.6, share: -0.18, percentile: 27.6, status: "温和流出", spark: [48,45,50,43,38,35,41,33,29,27,25,22] },
  { code: "000852", name: "中证1000", flow1d: -13.2, flow5d: -32.8, flow20d: -45.6, share: -0.26, percentile: 14.8, status: "流出偏强", spark: [55,52,48,50,43,39,35,29,31,24,20,17] },
  { code: "932000", name: "中证2000", flow1d: -6.7, flow5d: -18.9, flow20d: -27.4, share: -0.22, percentile: 19.3, status: "持续流出", spark: [46,49,45,40,43,36,31,33,27,24,20,18] },
  { code: "399006", name: "创业板指", flow1d: 5.9, flow5d: 14.2, flow20d: 38.7, share: 0.15, percentile: 62.1, status: "温和流入", spark: [26,30,28,35,32,39,42,38,45,48,46,52] },
  { code: "000688", name: "科创50", flow1d: 9.7, flow5d: 22.6, flow20d: 51.2, share: 0.28, percentile: 71.5, status: "流入增强", spark: [18,23,21,28,25,35,39,43,40,51,55,58] },
  { code: "000698", name: "科创100", flow1d: 3.2, flow5d: 8.6, flow20d: 17.8, share: 0.09, percentile: 54.7, status: "中性", spark: [31,29,34,32,37,35,40,38,42,41,45,44] },
  { code: "000510", name: "中证A500", flow1d: 21.4, flow5d: 56.3, flow20d: 94.8, share: 0.37, percentile: 82.9, status: "流入偏强", spark: [19,25,23,31,35,33,41,46,49,53,59,62] },
];

const ETF_DETAIL = [
  ["510300", "沪深300ETF", "华泰柏瑞", 563.8, 14.6, 26.1],
  ["510310", "沪深300ETF易方达", "易方达", 311.4, 7.8, 13.9],
  ["510330", "华夏沪深300ETF", "华夏", 286.9, 5.4, 9.6],
  ["159919", "沪深300ETF嘉实", "嘉实", 171.2, 3.7, 6.6],
] as const;

const NAV = ["资金总览", "指数资金", "国家队代理", "资金轮动", "历史分位", "数据与方法"];

function money(n: number) { return `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(1)} 亿`; }
function tone(n: number) { return n > 0 ? "up" : n < 0 ? "down" : "flat"; }

function Sparkline({ values, positive = true }: { values: number[]; positive?: boolean }) {
  const min = Math.min(...values), max = Math.max(...values);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${29 - ((v - min) / Math.max(1, max - min)) * 24}`).join(" ");
  return <svg className={`spark ${positive ? "spark-up" : "spark-down"}`} viewBox="0 0 100 32" preserveAspectRatio="none" aria-label="近12日趋势"><polyline points={pts} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>;
}

function Metric({ label, value, sub, valueTone = "neutral" }: { label: string; value: string; sub: string; valueTone?: "up" | "down" | "neutral" }) {
  return <article className="metric-card"><div className="metric-label">{label}<span>↗</span></div><div className={`metric-value ${valueTone}`}>{value}</div><div className="metric-sub">{sub}</div></article>;
}

export default function Home() {
  const [active, setActive] = useState("资金总览");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<"flow1d" | "flow5d" | "percentile">("flow1d");
  const [selected, setSelected] = useState<IndexRow | null>(null);
  const [period, setPeriod] = useState("20日");
  const rows = useMemo(() => INDEX_DATA.filter(x => x.name.includes(query) || x.code.includes(query)).sort((a,b) => b[sortKey] - a[sortKey]), [query, sortKey]);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">E</div><div><strong>ETF资金雷达</strong><small>ETF FLOW RADAR</small></div></div>
      <nav>{NAV.map((item, i) => <button key={item} className={active === item ? "active" : ""} onClick={() => { setActive(item); document.getElementById(`section-${i}`)?.scrollIntoView({behavior:"smooth"}); }}><span>{["⌂","▤","◎","⇄","◫","i"][i]}</span>{item}</button>)}</nav>
      <div className="side-foot"><span className="status-dot" /> 数据服务正常<small>研究系统 · v1.0</small></div>
    </aside>

    <main>
      <header className="topbar">
        <div className="crumb">ETF Flow Research <span>/</span> 市场总览</div>
        <div className="top-actions"><span className="demo">DEMO DATA</span><span className="date">数据日期 2026-08-11 · 18:32</span><button title="数据质量日志">✓ 数据正常</button></div>
      </header>

      <div className="content">
        <section id="section-0" className="hero">
          <div><p className="eyebrow">A-SHARE ETF CAPITAL FLOW RESEARCH</p><h1>资金方向，一目了然。</h1><p className="lead">聚合指数级 ETF 份额变化，识别资金流向、异常强度与历史位置。</p></div>
          <div className="asof"><span>最近交易日</span><strong>2026.08.11</strong><small>收盘后更新</small></div>
        </section>

        <section className="conclusion">
          <div className="section-kicker">今日 ETF 资金特征</div>
          <p>宽基 ETF 整体呈净申购状态，资金主要集中于<span>沪深300</span>、<span>中证A500</span>和<span>上证50</span>方向；大盘相对小盘的资金偏好进一步增强。</p>
          <div className="conclusion-rule"><b>规则生成</b> 基于指数聚合净流、轮动得分与历史分位，不使用生成式判断</div>
        </section>

        <section className="metrics-grid">
          <Metric label="今日宽基估算净流入" value="+63.3 亿" sub="9个核心指数合计" valueTone="up" />
          <Metric label="国家队代理活跃度" value="76.8" sub="较20日均值 +18.4%" valueTone="up" />
          <Metric label="大小盘资金偏好" value="大盘" sub="Rotation Score  +1.42" />
          <Metric label="ETF资金热度" value="84.6%" sub="近250日历史分位" valueTone="up" />
        </section>

        <section id="section-1" className="panel flow-panel">
          <div className="panel-head"><div><p className="section-kicker">CORE INDEX MONITOR</p><h2>核心宽基资金监控</h2></div><div className="controls"><label><span>⌕</span><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索指数 / 代码" /></label><select value={sortKey} onChange={e=>setSortKey(e.target.value as typeof sortKey)} aria-label="排序方式"><option value="flow1d">今日流入</option><option value="flow5d">5日流入</option><option value="percentile">历史分位</option></select></div></div>
          <div className="table-wrap"><table><thead><tr><th>指数</th><th>今日 Flow</th><th>5日 Flow</th><th>20日 Flow</th><th>份额变化</th><th>历史分位</th><th>近12日趋势</th><th>资金状态</th></tr></thead><tbody>{rows.map(row => <tr key={row.code} onClick={()=>setSelected(row)}><td><strong>{row.name}</strong><small>{row.code}</small></td><td className={tone(row.flow1d)}>{money(row.flow1d)}</td><td className={tone(row.flow5d)}>{money(row.flow5d)}</td><td className={tone(row.flow20d)}>{money(row.flow20d)}</td><td className={tone(row.share)}>{row.share > 0 ? "+" : ""}{row.share.toFixed(2)}%</td><td><div className="percent"><span style={{width:`${row.percentile}%`}} /><b>{row.percentile.toFixed(1)}%</b></div></td><td><Sparkline values={row.spark} positive={row.flow1d >= 0}/></td><td><span className={`pill ${tone(row.flow1d)}`}>{row.status}</span><button className="row-open" aria-label={`查看${row.name}详情`}>›</button></td></tr>)}</tbody></table></div>
          <div className="table-note">共 9 个核心指数 · 点击任意指数查看 ETF 构成与贡献度</div>
        </section>

        <section className="two-col">
          <article id="section-2" className="panel national">
            <div className="panel-head"><div><p className="section-kicker">NATIONAL TEAM PROXY</p><h2>国家队代理资金</h2></div><span className="info">代理指标 ⓘ</span></div>
            <div className="activity"><div className="gauge"><div><strong>76.8</strong><span>ACTIVITY</span></div></div><div className="activity-copy"><b>资金活跃度偏强</b><p>核心代理 ETF 连续 4 个交易日出现估算净申购，当前强度处于近一年较高区间。</p><div><span>1D <b className="up">+28.4亿</b></span><span>5D <b className="up">+71.6亿</b></span><span>分位 <b>81.3%</b></span></div></div></div>
            <div className="mini-bars">{[["沪深300",82],["上证50",68],["中证A500",74],["其他宽基",35]].map(([n,v])=><div key={n as string}><span>{n}</span><i><em style={{width:`${v}%`}}/></i><b>{v}</b></div>)}</div>
          </article>

          <article id="section-3" className="panel rotation">
            <div className="panel-head"><div><p className="section-kicker">CAPITAL ROTATION</p><h2>资金轮动</h2></div><select aria-label="轮动周期"><option>近20日</option><option>近60日</option></select></div>
            <div className="quadrant"><span className="axis-y">资金动量</span><span className="axis-x">资金强度</span><i className="q1">流入增强</i><i className="q2">流入减弱</i><i className="q3">流出增强</i><i className="q4">流出收敛</i><button className="bubble b1">大盘</button><button className="bubble b2">科技</button><button className="bubble b3">成长</button><button className="bubble b4">小盘</button><button className="bubble b5">红利</button></div>
            <div className="rotation-legend"><span><i className="red"/>大盘相对占优</span><span><i className="green"/>小盘资金承压</span><b>大小盘得分 +1.42</b></div>
          </article>
        </section>

        <section id="section-4" className="panel history">
          <div className="panel-head"><div><p className="section-kicker">HISTORICAL POSITION</p><h2>历史资金位置</h2></div><div className="segmented">{["5日","20日","60日"].map(x=><button key={x} className={period===x?"active":""} onClick={()=>setPeriod(x)}>{x}</button>)}</div></div>
          <div className="heat-head"><span>指数</span><span>流出极值</span><span>中性区间</span><span>流入极值</span><span>{period}分位</span><span>Z-Score</span></div>
          {INDEX_DATA.slice(0,7).map(r=><div className="heat-row" key={r.code}><b>{r.name}</b><div className="heat-track"><i style={{left:`${r.percentile}%`}}/><span style={{width:`${r.percentile}%`}}/></div><strong>{r.percentile.toFixed(1)}%</strong><em className={tone(r.flow20d)}>{(r.flow20d/38).toFixed(2)}</em></div>)}
        </section>

        <section id="section-5" className="method">
          <div><p className="section-kicker">METHODOLOGY</p><h2>数据与方法</h2><p>本系统观察 ETF 份额变化而非单纯规模变化。同一指数下的主要 ETF 按指数映射聚合，提供指数级与基金级两种研究层级。</p></div>
          <div className="formula"><span>估算资金流<sub>t</sub></span><b>=</b><strong>( 份额<sub>t</sub> − 份额<sub>t−1</sub> ) × 参考价格<sub>t</sub></strong><small>参考价格优先采用当日净值；不可得时使用当日收盘价。</small></div>
        </section>
      </div>
      <footer><div><strong>ETF资金雷达</strong><span>ETF FLOW RESEARCH SYSTEM</span></div><p>本平台数据及指标仅用于市场研究与信息展示，不构成任何投资建议。ETF资金流为根据基金份额变化及相关市场数据计算的估算结果。国家队代理资金指标仅用于观察特定宽基ETF的资金行为，不代表对实际投资主体身份的确认。</p></footer>
    </main>

    {selected && <div className="drawer-backdrop"><button className="backdrop-close" aria-label="关闭指数详情" onClick={()=>setSelected(null)} /><aside className="drawer"><button className="close" onClick={()=>setSelected(null)}>×</button><p className="section-kicker">INDEX DETAIL · {selected.code}</p><h2>{selected.name} ETF 资金监测</h2><div className="drawer-metrics"><Metric label="今日估算净流" value={money(selected.flow1d)} sub="指数级聚合" valueTone={selected.flow1d>0?"up":"down"}/><Metric label="20日估算净流" value={money(selected.flow20d)} sub="滚动累计" valueTone={selected.flow20d>0?"up":"down"}/></div><h3>ETF 构成与贡献</h3><table><thead><tr><th>基金</th><th>公司</th><th>份额(亿)</th><th>Flow</th><th>贡献</th></tr></thead><tbody>{ETF_DETAIL.map(x=><tr key={x[0]}><td><b>{x[1]}</b><small>{x[0]}</small></td><td>{x[2]}</td><td>{x[3]}</td><td className="up">+{x[4]}亿</td><td>{x[5]}%</td></tr>)}</tbody></table><div className="drawer-chart"><span>近60日指数聚合资金趋势</span><Sparkline values={[18,20,17,25,29,27,35,32,40,46,43,51,57,54,62,68]} /></div><div className="demo-note">DEMO DATA · 当前页面用于验证产品结构与交互，未接入生产数据源。</div></aside></div>}
  </div>;
}
