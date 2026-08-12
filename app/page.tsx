"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type IndexRow = {
  code: string; name: string; group: string; flow1d: number; shareChangePct: number;
  flow5d: number | null; flow20d: number | null; etfCount: number; percentile: number | null;
  status: string; spark: number[]; nationalTeamProxy: boolean;
};
type EtfRow = {
  code: string; name: string; exchange: string; indexCode: string; indexName: string;
  group: string; shares: number; previousShares: number; shareChangePct: number;
  referencePrice: number; referencePriceType: "NAV" | "CLOSE"; estimatedFlow: number; source: string;
};
type Snapshot = {
  status: "verified" | "warning" | "failed"; tradeDate: string; previousTradeDate: string; generatedAt: string; sourceMode: "REAL";
  quality: { marketEtfCount: number; priceCoverage: number; shareReconciliationRate: number | null; mappedEtfCount: number; issues: {severity:string;check:string;message:string}[] };
  sources: { name:string; field:string; role:string }[]; indices: IndexRow[]; etfs: EtfRow[]; methodology: string;
};

const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const money = (n: number | null) => n === null ? "—" : `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(2)} 亿`;
const tone = (n: number) => n > 0 ? "up" : n < 0 ? "down" : "flat";
const fullDate = (iso: string) => new Intl.DateTimeFormat("zh-CN", { year:"numeric", month:"long", day:"numeric", weekday:"short" }).format(new Date(`${iso}T12:00:00+08:00`));

function Stat({ label, value, note, valueTone="flat" }: {label:string;value:string;note:string;valueTone?:string}) {
  return <article className="stat"><span>{label}</span><strong className={valueTone}>{value}</strong><small>{note}</small></article>;
}

function FlowBar({ value, max }: {value:number;max:number}) {
  const width = Math.max(2, Math.abs(value) / Math.max(max, 0.01) * 48);
  return <div className="flowbar"><span className={tone(value)} style={value>=0?{left:"50%",width:`${width}%`}:{right:"50%",width:`${width}%`}} /><i /></div>;
}

export default function Home() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"flow1d"|"name"|"shareChangePct">("flow1d");
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
          if (snapshot.sourceMode !== "REAL" || snapshot.status === "failed") throw new Error("没有可发布的真实已验证快照");
          setData(snapshot); return;
        } catch (error) { lastError = error; }
      }
      setError(String(lastError instanceof Error ? lastError.message : lastError));
    })();
  }, []);

  const indices = useMemo(() => {
    if (!data) return [];
    return [...data.indices].filter(x=>x.name.includes(query)||x.code.includes(query)).sort((a,b)=>sort==="name"?a.name.localeCompare(b.name,"zh-CN"):b[sort]-a[sort]);
  }, [data, query, sort]);
  const maxFlow = Math.max(...indices.map(x=>Math.abs(x.flow1d)), 1);
  const totalFlow = data?.indices.reduce((s,x)=>s+x.flow1d,0) ?? 0;
  const inflow = data?.indices.filter(x=>x.flow1d>0).length ?? 0;
  const outflow = data?.indices.filter(x=>x.flow1d<0).length ?? 0;
  const proxyFlow = data?.indices.filter(x=>x.nationalTeamProxy).reduce((s,x)=>s+x.flow1d,0) ?? 0;
  const large = data?.indices.filter(x=>x.group==="大盘").reduce((s,x)=>s+x.flow1d,0) ?? 0;
  const small = data?.indices.filter(x=>x.group==="小盘").reduce((s,x)=>s+x.flow1d,0) ?? 0;

  const exportJpg = async () => {
    const node = reportRef.current; if (!node || !data) return;
    setExporting(true); node.classList.add("exporting");
    try {
      await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
      const { toJpeg } = await import("html-to-image");
      const url = await toJpeg(node, { quality:.98, pixelRatio:3, backgroundColor:"#f4f5f7", cacheBust:true,
        filter: el => !(el instanceof HTMLElement && el.dataset.exportHide === "true") });
      const a=document.createElement("a"); a.href=url; a.download=`资金ETF流动每日跟踪_${data.tradeDate}.jpg`; a.click();
    } catch (e) { alert(`导出失败：${String(e)}`); }
    finally { node.classList.remove("exporting"); setExporting(false); }
  };

  if (error) return <main className="blocked"><b>数据未发布</b><h1>没有通过质量门禁的真实数据</h1><p>{error}</p><small>系统不会回退到 DEMO 或伪造数据。请检查每日采集任务后重试。</small></main>;
  if (!data) return <main className="blocked"><b>REAL DATA</b><h1>正在读取已验证快照…</h1></main>;

  const leaders = [...data.indices].sort((a,b)=>b.flow1d-a.flow1d);
  const summary = totalFlow >= 0
    ? `核心宽基合计估算净流入 ${money(totalFlow)}，${leaders[0]?.name ?? "—"}流入居前；${leaders.at(-1)?.name ?? "—"}资金相对承压。`
    : `核心宽基合计估算净流出 ${money(totalFlow)}，${leaders.at(-1)?.name ?? "—"}流出居前；${leaders[0]?.name ?? "—"}表现相对较强。`;

  return <div className="site">
    <nav className="nav"><div className="mark">E</div><div className="nav-title"><strong>资金ETF流动每日跟踪</strong><span>ETF FLOW DAILY TRACKER</span></div><div className="nav-links"><a href="#overview">总览</a><a href="#indices">指数资金</a><a href="#rotation">轮动</a><a href="#quality">数据质量</a></div><button onClick={exportJpg} disabled={exporting} data-export-hide="true">{exporting?"正在生成…":"导出高清 JPG"}</button></nav>
    <div className="report" ref={reportRef}>
      <header className="report-head" id="overview">
        <div><p>A-SHARE ETF CAPITAL FLOW · VERIFIED DAILY SNAPSHOT</p><h1>资金ETF流动每日跟踪</h1></div>
        <div className="analysis-date"><span>每日分析日期</span><strong>{fullDate(data.tradeDate)}</strong><small>数据生成 {new Date(data.generatedAt).toLocaleString("zh-CN",{hour12:false})}</small></div>
      </header>

      <section className="trust-strip"><b><i />真实数据已验证</b><span>全市场 {fmt.format(data.quality.marketEtfCount)} 只 ETF</span><span>同日价格覆盖 {(data.quality.priceCoverage*100).toFixed(2)}%</span><span>同日份额对账 {data.quality.shareReconciliationRate===null?"待行情端同日数据":`${(data.quality.shareReconciliationRate*100).toFixed(2)}%`}</span><span>核心映射 {data.quality.mappedEtfCount} 只</span></section>

      <section className="summary"><div><span>规则化市场摘要</span><p>{summary}</p></div><aside><b>{inflow}</b><span>个指数净流入</span><i /> <b>{outflow}</b><span>个指数净流出</span></aside></section>

      <section className="stats-grid">
        <Stat label="核心宽基合计净流" value={money(totalFlow)} note={`${data.indices.length} 个核心指数聚合`} valueTone={tone(totalFlow)} />
        <Stat label="国家队代理 ETF 净流" value={money(proxyFlow)} note="仅为特定宽基代理观察" valueTone={tone(proxyFlow)} />
        <Stat label="大盘方向净流" value={money(large)} note="上证50 / 沪深300 / A500" valueTone={tone(large)} />
        <Stat label="小盘方向净流" value={money(small)} note="中证1000 / 中证2000" valueTone={tone(small)} />
        <Stat label="相对资金偏好" value={large-small>=0?"偏向大盘":"偏向小盘"} note={`大盘−小盘 ${money(large-small)}`} />
        <Stat label="数据状态" value={data.status==="verified"?"全部通过":"带提示通过"} note={`${data.quality.issues.length} 项质量提示`} />
      </section>

      <section className="panel" id="indices">
        <div className="panel-head"><div><span>CORE INDEX FLOW MONITOR</span><h2>核心指数资金监控</h2></div><div className="filters" data-export-hide="true"><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索指数 / 代码"/><select value={sort} onChange={e=>setSort(e.target.value as typeof sort)}><option value="flow1d">按净流排序</option><option value="shareChangePct">按份额变化</option><option value="name">按指数名称</option></select></div></div>
        <div className="flow-table"><div className="flow-tr flow-th"><span>指数 / 代码</span><span>方向</span><span>1日净流</span><span>5日累计</span><span>20日累计</span><span>份额变化</span><span>ETF数</span><span>250日位置</span><span>状态</span></div>{indices.map(row=><button className="flow-tr" key={row.code} onClick={()=>setSelected(row)}><span><b>{row.name}</b><small>{row.code} · {row.group}</small></span><FlowBar value={row.flow1d} max={maxFlow}/><strong className={tone(row.flow1d)}>{money(row.flow1d)}</strong><span className={row.flow5d===null?"flat":tone(row.flow5d)}>{money(row.flow5d)}</span><span className={row.flow20d===null?"flat":tone(row.flow20d)}>{money(row.flow20d)}</span><span className={tone(row.shareChangePct)}>{row.shareChangePct>=0?"+":""}{row.shareChangePct.toFixed(3)}%</span><span>{row.etfCount} 只</span><span>{row.percentile===null?"样本积累中":`${row.percentile.toFixed(1)}%`}</span><em>{row.status}</em></button>)}</div>
        <p className="method-note">估算资金流 =（T日总份额 − 前一交易日总份额）× T日单位净值（缺失时仅使用同日收盘价）。历史样本不足时 5日、20日和250日位置明确留空。</p>
      </section>

      <section className="split" id="rotation">
        <article className="panel compact"><div className="panel-head"><div><span>CAPITAL ROTATION</span><h2>资金轮动观察</h2></div></div><div className="rotation-list">{[["大盘",large],["小盘",small],["成长",data.indices.filter(x=>x.group==="成长").reduce((s,x)=>s+x.flow1d,0)],["科技",data.indices.filter(x=>x.group==="科技").reduce((s,x)=>s+x.flow1d,0)]].map(([name,value])=><div key={name as string}><b>{name}</b><FlowBar value={value as number} max={Math.max(Math.abs(large),Math.abs(small),1)}/><strong className={tone(value as number)}>{money(value as number)}</strong></div>)}</div><div className="signal"><span>规则结论</span><b>{large-small>=0?"资金相对偏向大盘":"资金相对偏向小盘"}</b><small>比较同一交易日两组指数的聚合估算净流，不代表未来收益判断。</small></div></article>
        <article className="panel compact"><div className="panel-head"><div><span>NATIONAL TEAM PROXY</span><h2>国家队代理资金</h2></div></div><div className="proxy-number"><span>代理池当日估算净流</span><strong className={tone(proxyFlow)}>{money(proxyFlow)}</strong><small>{data.indices.filter(x=>x.nationalTeamProxy).map(x=>x.name).join(" · ")}</small></div><p className="proxy-copy">本指标只观察预先定义的核心宽基 ETF 资金行为，不识别、推断或确认真实投资主体身份。</p></article>
      </section>

      <section className="panel" id="quality"><div className="panel-head"><div><span>DATA QUALITY & LINEAGE</span><h2>数据源与质量状态</h2></div><b className="verified">✓ {data.status.toUpperCase()}</b></div><div className="source-grid">{data.sources.map(x=><article key={x.name}><b>{x.name}</b><span>{x.role}</span><p>{x.field}</p></article>)}</div><div className="quality-grid"><span><b>日期锁定</b> 份额与价格必须属于 {data.tradeDate}</span><span><b>完整性</b> 同日参考价格覆盖≥95%</span><span><b>交易日</b> T−1 为 {data.previousTradeDate}</span><span><b>失败策略</b> 不覆盖上一已验证快照</span></div>{data.quality.issues.length>0&&<div className="quality-issues">{data.quality.issues.map(x=><span key={x.check}>{x.message}</span>)}</div>}</section>

      <footer><b>资金ETF流动每日跟踪</b><p>数据来源：上海证券交易所、深圳证券交易所、东方财富公开数据，由 AKShare 1.18.84 统一采集。本报告仅用于市场研究与信息展示，不构成投资建议。ETF资金流为份额变化乘以同日参考价格的估算值。</p><span>分析日期 {data.tradeDate}</span></footer>
    </div>

    {selected && <div className="drawer-bg"><button aria-label="关闭详情" onClick={()=>setSelected(null)}/><aside className="drawer"><button className="drawer-x" onClick={()=>setSelected(null)}>×</button><span>INDEX DETAIL · {selected.code}</span><h2>{selected.name}</h2><div className="drawer-stats"><Stat label="当日估算净流" value={money(selected.flow1d)} note={`${selected.etfCount} 只ETF聚合`} valueTone={tone(selected.flow1d)}/><Stat label="份额变化中位数" value={`${selected.shareChangePct>=0?"+":""}${selected.shareChangePct.toFixed(3)}%`} note="指数内ETF中位数" valueTone={tone(selected.shareChangePct)}/></div><h3>ETF 贡献明细</h3><div className="etf-list">{data.etfs.filter(x=>x.indexCode===selected.code).sort((a,b)=>Math.abs(b.estimatedFlow)-Math.abs(a.estimatedFlow)).map(x=><div key={x.code}><span><b>{x.name}</b><small>{x.code} · {x.exchange} · {x.referencePriceType}</small></span><span>份额 {fmt.format(x.shares/1e8)}亿</span><span>{x.shareChangePct>=0?"+":""}{x.shareChangePct.toFixed(3)}%</span><strong className={tone(x.estimatedFlow)}>{money(x.estimatedFlow/1e8)}</strong></div>)}</div><p>价格口径：同日单位净值优先、同日收盘价回退；份额口径：交易所日终总份额。</p></aside></div>}
  </div>;
}
