"""Single production entrypoint for ETF Flow Radar schema v6.

Transport and guard layers remain in the older modules. This file is the single
public production entrypoint and the single client-facing schema layer. Archived
rebuilds can call ``apply_v2_semantics`` with validated local share data, so a
methodology-only JSON migration does not need to redownload historical exchange
files.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded
import update_daily_production as production
import flow_model_v2
import flow_comparison_v2
import flow_scope_breakdown_v2

_ORIG_POSTPROCESS = production._postprocess_snapshot
_ORIG_ATOMIC_PUBLISH = base.atomic_publish


def _load_secondary_spot(day: date) -> pd.DataFrame:
    path = base.PUBLIC / "order_flow" / f"{day.isoformat()}.json"
    if path.exists():
        payload = json.loads(path.read_text("utf-8"))
        if (
            payload.get("tradeDate") == day.isoformat()
            and payload.get("metric") in {"secondaryMarketMainOrderFlow", "secondaryMarketETFTradingFlow"}
        ):
            rows = payload.get("etfs", [])
            if rows:
                frame = pd.DataFrame(rows)
                frame["代码"] = frame["code"].astype(str).str.zfill(6)
                frame["名称"] = frame["name"].astype(str)
                frame["数据日期"] = day.isoformat()
                if "mainOrderFlow1d" in frame.columns:
                    frame["主力净流入-净额"] = pd.to_numeric(frame["mainOrderFlow1d"], errors="coerce") * 1e8
                if "tradeNetFlow1d" in frame.columns:
                    frame["当日交易净额"] = pd.to_numeric(frame["tradeNetFlow1d"], errors="coerce") * 1e8
                if "tradeInflow1d" in frame.columns:
                    frame["当日交易流入"] = pd.to_numeric(frame["tradeInflow1d"], errors="coerce") * 1e8
                if "tradeOutflow1d" in frame.columns:
                    frame["当日交易流出"] = pd.to_numeric(frame["tradeOutflow1d"], errors="coerce") * 1e8
                if "amount" in frame.columns:
                    frame["成交额"] = pd.to_numeric(frame["amount"], errors="coerce") * 1e8
                keep = [
                    c for c in [
                        "代码", "名称", "主力净流入-净额", "当日交易净额",
                        "当日交易流入", "当日交易流出", "成交额", "数据日期",
                    ] if c in frame.columns
                ]
                return frame[keep]
    return guarded._get_spot()


def _same_day_trade_rows(spot: pd.DataFrame | None, day: date) -> pd.DataFrame:
    """Return same-day per-ETF all-trade net flow in yuan."""
    columns = ["code", "trade_net_yuan", "trade_in_yuan", "trade_out_yuan"]
    if spot is None or spot.empty:
        return pd.DataFrame(columns=columns)
    source = spot.copy()
    source.columns = [str(c).strip() for c in source.columns]
    if not {"代码", "数据日期"}.issubset(source.columns):
        return pd.DataFrame(columns=columns)
    source["code"] = source["代码"].astype(str).str.zfill(6)
    source["data_date"] = pd.to_datetime(source["数据日期"], errors="coerce").dt.date
    source = source[source["data_date"] == day].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)
    if "当日交易净额" in source.columns:
        source["trade_net_yuan"] = pd.to_numeric(source["当日交易净额"], errors="coerce")
        source["trade_in_yuan"] = pd.to_numeric(source.get("当日交易流入"), errors="coerce")
        source["trade_out_yuan"] = pd.to_numeric(source.get("当日交易流出"), errors="coerce")
    elif {"成交额", "外盘", "内盘"}.issubset(source.columns):
        amount = pd.to_numeric(source["成交额"], errors="coerce")
        outer = pd.to_numeric(source["外盘"], errors="coerce")
        inner = pd.to_numeric(source["内盘"], errors="coerce")
        directional = outer + inner
        valid = directional > 0
        source["trade_in_yuan"] = amount * outer / directional.where(valid)
        source["trade_out_yuan"] = amount * inner / directional.where(valid)
        source["trade_net_yuan"] = source["trade_in_yuan"] - source["trade_out_yuan"]
    else:
        return pd.DataFrame(columns=columns)
    return source[columns].dropna(subset=["trade_net_yuan"]).drop_duplicates("code", keep="last")


def _add_trade_net_flow(snapshot: dict[str, Any], day: date, ths: pd.DataFrame, spot: pd.DataFrame | None) -> None:
    """Add a separate secondary-market trading statistic without changing primary flow."""
    target = {
        "metric": "secondaryMarketTradeNetFlowEstimate",
        "displayName": "当日成交资金净流入/净流出",
        "definition": "按同日ETF成交额与外盘/内盘主动成交方向估算交易资金净额；只显示主动买入金额减主动卖出金额的差额。",
        "source": "东方财富ETF同日行情 成交额 + 外盘/内盘",
        "tradeDate": day.isoformat(),
        "status": "unavailable",
        "scopeTotals": {},
    }
    exact = _same_day_trade_rows(spot, day)
    if exact.empty:
        target["reason"] = "no same-day all-trade direction snapshot"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return
    universe = pd.DataFrame(snapshot.get("universe", []))
    if universe.empty or not {"code", "name"}.issubset(universe.columns):
        target["reason"] = "snapshot universe missing code/name"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return
    frame = universe[["code", "name"]].copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame.drop_duplicates("code", keep="last").merge(ths[["code", "fund_name", "fund_type"]], on="code", how="left")
    frame["scope"] = frame.apply(lambda row: flow_model_v2._asset_scope(str(row["name"]), str(row.get("fund_name", "")), str(row.get("fund_type", ""))), axis=1)
    joined = frame[["code", "scope"]].merge(exact, on="code", how="inner")
    if joined.empty:
        target["reason"] = "same-day trading rows do not overlap the ETF universe"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return

    def total(part: pd.DataFrame) -> dict[str, Any]:
        inflow = pd.to_numeric(part["trade_in_yuan"], errors="coerce")
        outflow = pd.to_numeric(part["trade_out_yuan"], errors="coerce")
        return {
            "etfCount": int(len(part)),
            "netFlow1d": round(float(part["trade_net_yuan"].sum()) / 1e8, 2),
            "inflow1d": round(float(inflow.sum()) / 1e8, 2) if inflow.notna().any() else None,
            "outflow1d": round(float(outflow.sum()) / 1e8, 2) if outflow.notna().any() else None,
        }
    target.update({"status": "available", "scopeTotals": {
        "allEtf": total(joined),
        "stockEtfIncludingCrossBorder": total(joined[joined["scope"].isin(["aShareStockEtf", "crossBorderStockEtf"])]),
        "aShareStockEtf": total(joined[joined["scope"].eq("aShareStockEtf")]),
    }})
    snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
    trade_map = dict(zip(joined["code"], joined["trade_net_yuan"] / 1e8))
    for item in snapshot.get("etfs", []):
        value = trade_map.get(str(item.get("code", "")).zfill(6))
        item["secondaryTradeNetFlow1d"] = round(float(value), 4) if pd.notna(value) else None


def _flow_phrase(value: float) -> str:
    if value > 0:
        return f"净流入{value:.1f}亿元"
    if value < 0:
        return f"净流出{abs(value):.1f}亿元"
    return "净额0.0亿元"


def _relative_intensity(value: float, base_value: float | None) -> float | None:
    if not isinstance(base_value, (int, float)) or not pd.notna(base_value) or float(base_value) <= 0:
        return None
    return abs(float(value)) / float(base_value) * 100.0


def _trade_strength(trade_value: float, turnover: float | None) -> str:
    intensity = _relative_intensity(trade_value, turnover)
    if intensity is None:
        return "balanced" if trade_value == 0 else "generic"
    if intensity < 1.0:
        return "balanced"
    if intensity < 3.0:
        return "small"
    if intensity < 6.0:
        return "clear"
    return "large"


def _primary_strength(primary_value: float, aum: float | None) -> str:
    intensity = _relative_intensity(primary_value, aum)
    if primary_value == 0:
        return "flat"
    if intensity is None:
        return "generic"
    if intensity < 0.05:
        return "flat"
    if intensity < 0.20:
        return "small"
    if intensity < 0.50:
        return "clear"
    return "large"


def _trade_copy(trade_value: float, strength: str) -> str:
    if strength == "balanced":
        return "A股ETF盘中买卖力量基本均衡"
    if strength == "generic":
        if trade_value > 0:
            return f"A股ETF盘中主动买入净额{trade_value:.1f}亿元"
        return f"A股ETF盘中主动卖出净额{abs(trade_value):.1f}亿元"
    if trade_value > 0:
        label = {"small": "买盘小幅偏强", "clear": "买盘偏强", "large": "买盘明显占优"}[strength]
        return f"A股ETF盘中{label}，主动买入净额{trade_value:.1f}亿元"
    label = {"small": "卖盘小幅偏强", "clear": "卖盘偏强", "large": "卖盘明显占优"}[strength]
    return f"A股ETF盘中{label}，主动卖出净额{abs(trade_value):.1f}亿元"


def _primary_copy(primary_value: float, strength: str) -> str:
    if strength == "flat":
        return "ETF份额对应申赎资金基本持平"
    qualifier = {"small": "小幅", "clear": "明显", "large": "大幅"}.get(strength, "")
    if primary_value > 0:
        return f"ETF份额对应申赎资金{qualifier}净流入{primary_value:.1f}亿元"
    return f"ETF份额对应申赎资金{qualifier}净流出{abs(primary_value):.1f}亿元"


def _motion_copy(primary_value: float, strength: str) -> str:
    if strength == "flat":
        return "整体资金暂无明显增减"
    if primary_value > 0:
        return {
            "small": "整体资金小幅净流入",
            "clear": "整体资金呈较明显净流入",
            "large": "整体资金净流入幅度较大",
        }.get(strength, "整体资金呈净流入")
    return {
        "small": "整体资金小幅净流出",
        "clear": "整体资金呈较明显净流出",
        "large": "整体资金净流出幅度较大",
    }.get(strength, "整体资金呈净流出")


def _market_flow_headline(
    trade_value: float | None,
    primary_value: float,
    trade_turnover: float | None = None,
    market_aum: float | None = None,
) -> str:
    """Translate observable flow direction and relative scale into calibrated client copy.

    先按交易净额占成交额、申赎金额占ETF规模的比例划分强弱；单日小幅
    申赎只描述为边际情绪变化，不外推为趋势性进出场。仅当规模较大且盘中
    交易、份额申赎同步时，才提示资金情绪明显变化，且始终保留持续性观察。
    """
    primary_strength = _primary_strength(primary_value, market_aum)
    primary_text = _primary_copy(primary_value, primary_strength)

    if trade_value is None:
        if primary_strength == "flat":
            interpretation = "份额端资金基本持平，暂无明确方向。"
        elif primary_strength == "small":
            direction = "流入" if primary_value > 0 else "流出"
            interpretation = f"份额端仅小幅净{direction}，单日变动有限，暂不宜外推为趋势性资金变化。"
        elif primary_strength == "generic":
            direction = "净流入" if primary_value > 0 else "净流出"
            interpretation = f"份额端方向显示为{direction}，但缺少可比规模基准，暂不对资金力度作延伸判断。"
        else:
            interpretation = f"{_motion_copy(primary_value, primary_strength)}，需结合后续数据观察持续性。"
        return f"A股ETF盘中主动买卖数据暂缺；{primary_text}\n—— {interpretation}"

    trade_strength = _trade_strength(trade_value, trade_turnover)
    trade_text = _trade_copy(trade_value, trade_strength)

    if trade_strength == "generic" or primary_strength == "generic":
        if trade_strength == "balanced":
            relation = "盘面交易相对平稳，份额端存在方向变化"
        elif primary_strength == "flat":
            relation = "盘中存在主动交易，份额端基本持平"
        else:
            same_direction = (trade_value > 0 and primary_value > 0) or (trade_value < 0 and primary_value < 0)
            relation = "盘中与份额端方向一致" if same_direction else "盘中与份额端方向分化"
        return f"{trade_text}；{primary_text}\n—— {relation}，但缺少可比规模基准，暂不对资金力度作延伸判断。"

    if trade_strength == "balanced":
        if primary_strength == "flat":
            return f"{trade_text}；{primary_text}\n—— 盘面交易与份额申赎均无明显方向，短线资金以观望为主。"
        if primary_strength == "small":
            direction = "流入" if primary_value > 0 else "流出"
            return f"{trade_text}；{primary_text}\n—— 盘面交易相对平稳，份额端仅小幅净{direction}，尚未形成明确资金方向。"
        direction = "净流入" if primary_value > 0 else "净流出"
        return f"{trade_text}；{primary_text}\n—— 盘面交易相对平稳，份额端{direction}较为明显，需观察是否持续。"

    if primary_strength == "flat":
        side = "买盘" if trade_value > 0 else "卖盘"
        return f"{trade_text}；{primary_text}\n—— 盘中{side}偏强，但份额端基本持平，暂未见明显申赎跟随。"

    same_direction = (trade_value > 0 and primary_value > 0) or (trade_value < 0 and primary_value < 0)
    if same_direction:
        if primary_strength == "small":
            if primary_value > 0:
                interpretation = "盘面买盘与份额端均有小幅流入，资金情绪略有改善，尚未形成强增量信号。"
            else:
                interpretation = "盘面卖盘与份额端均偏弱，但份额端仅小幅净流出，短线资金略偏谨慎，尚未出现集中赎回压力。"
            return f"{trade_text}；{primary_text}\n—— {interpretation}"

        if primary_value > 0:
            if primary_strength == "large" and trade_strength == "large":
                interpretation = "盘面买盘与份额端大额净流入同步，资金增配意愿明显升温，仍需观察延续性。"
            else:
                interpretation = "盘面买盘与份额端同步净流入，资金增配意愿有所升温，仍需观察延续性。"
        elif primary_strength == "large" and trade_strength in {"clear", "large"}:
            interpretation = "盘面卖压与份额端大额净流出同步，资金情绪明显转弱，需跟踪后续是否持续。"
        else:
            interpretation = "盘面卖压与份额端净流出同步，资金情绪偏谨慎，尚需观察后续是否形成连续赎回。"
        return f"{trade_text}；{primary_text}\n—— {interpretation}"

    if trade_value < 0:
        if primary_strength == "small":
            interpretation = "盘面卖压下份额端仅小幅申购，存在轻微承接，力度仍有限。"
        else:
            interpretation = "盘面卖压下份额端净流入较为明显，一级市场存在承接，但持续性仍待确认。"
        return f"{trade_text}；但{primary_text}\n—— {interpretation}"

    if primary_strength == "small":
        interpretation = "盘面买盘偏强，份额端仅小幅净流出，可能存在短线兑现，尚不足以说明资金趋势。"
    else:
        interpretation = "盘面买盘偏强，但份额端净流出较为明显，二级与申赎行为分化，需观察持续性。"
    return f"{trade_text}；但{primary_text}\n—— {interpretation}"


def _visible_sector_groups(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Mutually-exclusive SW-level/theme groups actually rendered to clients."""
    return [g for g in snapshot.get("groups", []) if g.get("kind") == "industry"]


def _regenerate_v2_conclusion(snapshot: dict[str, Any]) -> None:
    """Build the client headline from one consistent visible classification layer."""
    production._regenerate_conclusion(snapshot)
    market = snapshot["market"]
    primary_value = market.get("flow1d")
    if not isinstance(primary_value, (int, float)):
        raise ValueError("A-share stock ETF market.flow1d is required for the homepage headline")
    trade_scope = snapshot.get("flowMetrics", {}).get("secondaryMarketTradeFlow", {}).get("scopeTotals", {}).get("aShareStockEtf", {})
    raw_trade_value = trade_scope.get("netFlow1d")
    trade_value = float(raw_trade_value) if isinstance(raw_trade_value, (int, float)) and pd.notna(raw_trade_value) else None
    raw_inflow = trade_scope.get("inflow1d")
    raw_outflow = trade_scope.get("outflow1d")
    trade_turnover = None
    if isinstance(raw_inflow, (int, float)) and isinstance(raw_outflow, (int, float)) and pd.notna(raw_inflow) and pd.notna(raw_outflow):
        trade_turnover = float(raw_inflow) + float(raw_outflow)
    raw_aum = market.get("aum")
    market_aum = float(raw_aum) if isinstance(raw_aum, (int, float)) and pd.notna(raw_aum) else None
    flow_headline = _market_flow_headline(trade_value, float(primary_value), trade_turnover, market_aum)

    groups = snapshot.get("groups", [])
    broad = [g for g in groups if g.get("kind") == "broad"]
    styles = [g for g in groups if g.get("kind") == "style"]
    sectors = _visible_sector_groups(snapshot)

    if broad:
        broad_in_count = sum(float(g.get("flow1d", 0) or 0) > 0 for g in broad)
        broad_out_count = sum(float(g.get("flow1d", 0) or 0) < 0 for g in broad)
        broad_stat = f"共{len(broad)}组，{broad_out_count}个净流出、{broad_in_count}个净流入"
        out_groups = [g for g in sorted(broad, key=lambda g: float(g.get("flow1d", 0) or 0)) if float(g.get("flow1d", 0) or 0) < 0]
        in_groups = [g for g in sorted(broad, key=lambda g: float(g.get("flow1d", 0) or 0), reverse=True) if float(g.get("flow1d", 0) or 0) > 0]
        details = []
        if out_groups:
            details.append("净流出居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in out_groups[:2]))
        if in_groups:
            details.append("净流入居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in in_groups[:2]))
        broad_fact = f"{broad_stat}；" + "；".join(details) + "。" if details else f"{broad_stat}；当日无明显资金方向。"
    else:
        broad_fact = "暂无可分析宽基ETF。"

    if styles:
        style_in = [g for g in sorted(styles, key=lambda g: float(g.get("flow1d", 0) or 0), reverse=True) if float(g.get("flow1d", 0) or 0) > 0]
        style_out = [g for g in sorted(styles, key=lambda g: float(g.get("flow1d", 0) or 0)) if float(g.get("flow1d", 0) or 0) < 0]
        parts = []
        if style_in:
            parts.append("净流入居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in style_in[:2]))
        if style_out:
            parts.append("净流出居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in style_out[:2]))
        style_fact = "；".join(parts) + "。" if parts else f"共{len(styles)}组，当日均无明显资金方向。"
    else:
        style_fact = "暂无可分析风格ETF。"

    if sectors:
        sector_in = [g for g in sorted(sectors, key=lambda g: float(g.get("flow1d", 0) or 0), reverse=True) if float(g.get("flow1d", 0) or 0) > 0]
        sector_out = [g for g in sorted(sectors, key=lambda g: float(g.get("flow1d", 0) or 0)) if float(g.get("flow1d", 0) or 0) < 0]
        parts = []
        if sector_in:
            parts.append("净流入居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in sector_in[:2]))
        if sector_out:
            parts.append("净流出居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in sector_out[:2]))
        sector_fact = "；".join(parts) + "。" if parts else "当日均无明显资金方向。"
    else:
        sector_fact = "暂无可分析申万一级与主题行业ETF。"

    etf_flows = [e for e in snapshot.get("etfs", []) if isinstance(e.get("flow1d"), (int, float)) and pd.notna(e.get("flow1d"))]
    single_in = max((e for e in etf_flows if float(e["flow1d"]) > 0), key=lambda e: float(e["flow1d"]), default=None)
    single_out = min((e for e in etf_flows if float(e["flow1d"]) < 0), key=lambda e: float(e["flow1d"]), default=None)
    single_parts = []
    if single_in:
        single_parts.append(f"净流入最大为{single_in['name']}{float(single_in['flow1d']):+.1f}亿")
    if single_out:
        single_parts.append(f"净流出最大为{single_out['name']}{float(single_out['flow1d']):+.1f}亿")
    single_fact = "；".join(single_parts) + "。" if single_parts else "当日无单只ETF显著资金变化。"

    facts = [broad_fact, style_fact, sector_fact, single_fact]
    snapshot["conclusion"]["facts"] = facts
    snapshot["conclusion"]["headline"] = flow_headline


def apply_v2_semantics(snapshot: dict[str, Any], day: date, share_window: list[tuple[date, pd.DataFrame]], ths: pd.DataFrame, spot: pd.DataFrame | None) -> None:
    """Apply every client-facing schema-v6 flow rule exactly once."""
    legacy_classified_count = snapshot.get("quality", {}).get("classifiedEtfCount")
    flow_model_v2.apply_flow_model(snapshot, day, share_window, ths, spot)
    flow_comparison_v2.add_primary_valuation_comparisons(snapshot)
    flow_scope_breakdown_v2.add_asset_class_totals(snapshot)
    _add_trade_net_flow(snapshot, day, ths, spot)
    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["displayName"] = "ETF当日净申购/赎回估算"
    primary["definition"] = "（T日交易所日终份额－T-1日公司行动调整后的可比份额）×T日单位净值。"
    rollups = production._build_industry_rollups(snapshot)
    snapshot["industryRollups"] = rollups
    snapshot["themeGroups"] = [g for g in snapshot.get("groups", []) if g.get("kind") == "industry" and g.get("parent")]

    market = snapshot["market"]
    classified_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in snapshot.get("groups", [])), 2)
    visible_sectors = _visible_sector_groups(snapshot)
    visible_sector_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in visible_sectors), 2)
    rollup_sector_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in rollups), 2)
    sector_in = max(visible_sectors, key=lambda g: float(g.get("flow1d", 0) or 0)) if visible_sectors else None
    sector_out = min(visible_sectors, key=lambda g: float(g.get("flow1d", 0) or 0)) if visible_sectors else None
    quality = snapshot.setdefault("quality", {})
    quality.update({
        "industryRollupCount": len(rollups), "themeGroupCount": len(snapshot["themeGroups"]),
        "legacyClassifiedEtfCountBeforeV6": legacy_classified_count, "classifiedEtfCount": len(snapshot.get("etfs", [])),
        "marketScopeEtfCount": market.get("etfCount"), "marketScope5dCount": market.get("etfCount5d"), "marketScope20dCount": market.get("etfCount20d"),
        "marketScopeSource": "同花顺精确交易日基金类型/NAV + 沪深交易所日终份额",
        "classifiedCoverageOfMarketPct": round(len(snapshot.get("etfs", [])) / max(int(market.get("etfCount") or 1), 1) * 100, 2),
        "marketScopeReconciliation": {"aShareEquityShareFlow1d": market.get("flow1d"), "classifiedGroupShareFlow1d": classified_flow, "ungroupedDifference": round(float(market.get("flow1d") or 0) - classified_flow, 2)},
        "clientSectorReconciliation": {
            "visibleGroupCount": len(visible_sectors), "visibleGroupFlow1d": visible_sector_flow,
            "industryRollupFlow1d": rollup_sector_flow, "difference": round(visible_sector_flow - rollup_sector_flow, 2),
            "topInflowGroup": None if sector_in is None else {"id": sector_in.get("id"), "name": sector_in.get("name"), "flow1d": sector_in.get("flow1d")},
            "topOutflowGroup": None if sector_out is None else {"id": sector_out.get("id"), "name": sector_out.get("name"), "flow1d": sector_out.get("flow1d")},
            "displayLayer": "mutually_exclusive_sw_level_and_theme_groups",
        },
    })
    snapshot["schemaVersion"] = 6
    snapshot.setdefault("methodology", {}).update({
        "flow": "ETF当日净流入/净流出估算 =（T日交易所日终份额 − T-1日公司行动调整后的可比份额）× T日单位净值。T-1只作为T日份额变化的基准；该结果就是T日净申购/赎回的资金估算，不是再与上一日资金流做一次比较。",
        "metricSeparation": "首页结论把两类数据翻译成易懂话术：盘中买盘/卖盘强弱来自同日成交额与外盘/内盘估算的主动买卖净额；ETF份额对应申赎资金来自交易所T日与T-1可比份额变化×T日NAV。盘中强弱按主动买卖净额占总成交额比例分为基本均衡、小幅偏强、偏强、明显占优；ETF份额资金按当日资金变化占A股股票ETF总规模比例分为基本持平、小幅、明显、大幅。系统再结合两者方向判断一致或背离。",
        "multiDay": "5日/20日当前字段为端点份额变化×期末单位净值，字段明确标记 Endpoint；不是逐日净流入额之和。schema v6开始落盘每日单ETF份额flow1d，积累足够交易日后再生成真正5日/20日累计净流入额。",
        "scope": "首页主指标固定使用A股股票ETF范围，不含跨境股票ETF、债券ETF、货币ETF和商品ETF；同时保留全部ETF、股票ETF（含跨境）和六类资产范围用于审计与对照。",
        "valuation": "ETF当日净流入/净流出主口径使用同日单位净值；flowMetrics.primaryMarket.valuationComparisons 同时保存同一份额变化按成交均价估值的对照总额。",
        "sectorDisplay": "客户端行业结论、排名与行业资金坐标统一使用互斥的“申万一级行业+热门主题”展示层；industryRollups仅用于把热门主题回卷到申万一级行业做审计，不参与客户端最大流入/流出排名。",
        "coordinates": "横轴 = 20日相对沪深300收益率；纵轴 = 5日端点份额变化×期末NAV ÷ 5日前参考规模（%）。",
    })
    _regenerate_v2_conclusion(snapshot)


def _v2_postprocess(snapshot: dict[str, Any], day: date) -> None:
    _ORIG_POSTPROCESS(snapshot, day)
    ths = production._get_ths_day(day)
    apply_v2_semantics(snapshot, day, production._LAST_WINDOW, ths, _load_secondary_spot(day))


def daily_flow_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    return {
        "schemaVersion": 1, "tradeDate": snapshot["tradeDate"], "generatedAt": snapshot["generatedAt"],
        "metric": "primaryMarketNetSubscriptionEstimate", "valuation": "sameDayUnitNAV",
        "marketScopes": primary.get("scopeTotals", {}), "assetClassTotals": primary.get("assetClassTotals", {}),
        "valuationComparisons": primary.get("valuationComparisons", {}),
        "etfs": [{"code": item.get("code"), "name": item.get("name"), "groupId": item.get("groupId"), "shares": item.get("shares"), "previousComparableShares": item.get("previousComparableShares"), "shareDelta1d": item.get("shareDelta1d"), "nav": item.get("nav"), "flow1d": item.get("flow1d"), "flow1dAvgPriceEstimate": item.get("flow1dAvgPriceEstimate")} for item in snapshot.get("etfs", [])],
    }


def _v2_atomic_publish(snapshot: dict[str, Any]) -> Path:
    path = _ORIG_ATOMIC_PUBLISH(snapshot)
    daily_dir = base.PUBLIC / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(daily_flow_payload(snapshot), ensure_ascii=False, indent=2)
    (daily_dir / f'{snapshot["tradeDate"]}.json').write_text(text, "utf-8")
    (daily_dir / "latest.json").write_text(text, "utf-8")
    return path


def install_v2_pipeline() -> None:
    production._postprocess_snapshot = _v2_postprocess
    production.install_production_pipeline()
    base.atomic_publish = _v2_atomic_publish


def main() -> int:
    install_v2_pipeline()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())