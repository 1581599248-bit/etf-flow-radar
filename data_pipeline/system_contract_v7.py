"""Unified semantic and reconciliation contract for ETF Flow Radar.

This layer does not replace the exchange-share collection pipeline.  It makes
all client-facing modules consume one canonical set of economic facts and one
set of words for those facts.

Canonical facts
---------------
1. Primary-market one-day estimate:
   (T end-of-day exchange shares - comparable T-1 shares) * T unit NAV.
2. Secondary-market trade statistics are trading-direction indicators only.
   They never overwrite, calibrate or rename the primary-market estimate.
3. Five-/twenty-session endpoint share changes are not cumulative cash flow.
   True cumulative values are published only when every daily primary-flow fact
   for the required official sessions is present.
4. Classification is a research mapping.  Ambiguous name-rule matches are
   excluded from client groups but remain inside the complete market universe.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

import flow_model_v2
import update_daily as base
import update_daily_production as production

CONTRACT_VERSION = "7.0"
DIRECTION_EPS_SHARES = 0.5

ASSET_SCOPES = (
    "aShareStockEtf",
    "crossBorderStockEtf",
    "bondEtf",
    "moneyEtf",
    "commodityEtf",
    "otherEtf",
)

# These guards target broad name fragments that can create a false sense of
# precision.  A guarded ETF stays in market totals and is merely removed from
# the client classification layer until an explicit mapping is available.
_AMBIGUITY_GUARDS: dict[str, tuple[re.Pattern[str], re.Pattern[str], str]] = {
    "sw_food_beverage": (
        re.compile(r"消费", re.I),
        re.compile(r"食品|饮料|白酒|啤酒|乳业|乳品|调味|酒ETF|食品饮料", re.I),
        "generic consumer name is not sufficient to claim food-and-beverage exposure",
    ),
    "sw_basic_chemicals": (
        re.compile(r"材料|新材", re.I),
        re.compile(r"化工|化学|化纤|基础化工", re.I),
        "generic materials name is not sufficient to claim basic-chemicals exposure",
    ),
    "sw_petrochemical": (
        re.compile(r"能源", re.I),
        re.compile(r"石油|石化|油气|原油", re.I),
        "generic energy name is not sufficient to claim petrochemical exposure",
    ),
    "sw_home_appliances": (
        re.compile(r"家居", re.I),
        re.compile(r"家电|家用电器", re.I),
        "generic home-furnishing name is not sufficient to claim home-appliance exposure",
    ),
    "sw_machinery": (
        re.compile(r"高端制造|智能制造|装备", re.I),
        re.compile(r"机械|机器人|工业母机|机床|自动化设备", re.I),
        "broad manufacturing name is not sufficient to claim machinery exposure",
    ),
    "sw_retail": (
        re.compile(r"可选消费|消费", re.I),
        re.compile(r"零售|商贸|电商|互联网电商", re.I),
        "generic discretionary-consumption name is not sufficient to claim retail exposure",
    ),
    "elec_chip": (
        re.compile(r"芯", re.I),
        re.compile(r"芯片|半导体|集成电路|晶圆|封测|光刻", re.I),
        "single-character chip match is ambiguous",
    ),
    "comp_ai": (
        re.compile(r"人工|智能", re.I),
        re.compile(r"人工智能|\bAI\b|算力|智算|云计算|大数据|数据中心|数据要素|东数西算", re.I),
        "generic intelligent/AI fragment is ambiguous",
    ),
}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _canonical_direction(delta: Any) -> str | None:
    if not _finite(delta):
        return None
    value = float(delta)
    if abs(value) < DIRECTION_EPS_SHARES:
        return "unchanged"
    return "increase" if value > 0 else "decrease"


def _flow_word(value: float) -> str:
    if value > 0:
        return f"净申购估算{value:.1f}亿元"
    if value < 0:
        return f"净赎回估算{abs(value):.1f}亿元"
    return "净申购/赎回估算0.0亿元"


def _set_direction_fields(row: dict[str, Any], delta_key: str = "shareDelta1d") -> None:
    delta = row.get(delta_key)
    direction = _canonical_direction(delta)
    if direction is None:
        return
    row["shareDirection1d"] = direction
    if direction == "unchanged" and _finite(delta) and float(delta) != 0:
        row.setdefault("shareDelta1dRaw", float(delta))
        row[delta_key] = 0.0
        for key in ("primaryFlow1d", "flow1d"):
            if key in row and _finite(row.get(key)):
                row.setdefault(f"{key}Raw", float(row[key]))
                row[key] = 0.0


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {"increase": 0, "decrease": 0, "unchanged": 0}
    for row in rows:
        direction = row.get("shareDirection1d")
        if direction in out:
            out[direction] += 1
    return out


def _scope_rows(snapshot: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    rows = [r for r in snapshot.get("universe", []) if _finite(r.get("primaryFlow1d"))]
    if scope == "allEtf":
        return rows
    if scope == "stockEtfIncludingCrossBorder":
        return [r for r in rows if r.get("assetScope") in {"aShareStockEtf", "crossBorderStockEtf"}]
    return [r for r in rows if r.get("assetScope") == scope]


def _reconcile_scope_row(existing: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(existing)
    counts = _counts(rows)
    result["etfCount"] = len(rows)
    result["flow1d"] = round(sum(float(r.get("primaryFlow1d") or 0) for r in rows), 2)
    result["increaseEtfCount1d"] = counts["increase"]
    result["decreaseEtfCount1d"] = counts["decrease"]
    result["unchangedEtfCount1d"] = counts["unchanged"]
    result["unchangedEtfPct1d"] = round(counts["unchanged"] / len(rows) * 100, 2) if rows else 0.0
    result["breadth1d"] = round((counts["increase"] - counts["decrease"]) / len(rows) * 100, 1) if rows else 0.0
    if rows:
        top_in = max(rows, key=lambda r: float(r.get("primaryFlow1d") or 0))
        top_out = min(rows, key=lambda r: float(r.get("primaryFlow1d") or 0))
        result["topInflowEtf"] = {
            "code": str(top_in.get("code")),
            "name": str(top_in.get("name")),
            "flow1d": round(float(top_in.get("primaryFlow1d") or 0), 2),
        }
        result["topOutflowEtf"] = {
            "code": str(top_out.get("code")),
            "name": str(top_out.get("name")),
            "flow1d": round(float(top_out.get("primaryFlow1d") or 0), 2),
        }
    return result


def canonicalize_directions_and_totals(snapshot: dict[str, Any]) -> None:
    """Use one direction decision and one total for every module."""
    universe_by_code: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("universe", []):
        _set_direction_fields(row)
        universe_by_code[str(row.get("code", "")).zfill(6)] = row

    for row in snapshot.get("etfs", []):
        code = str(row.get("code", "")).zfill(6)
        canonical = universe_by_code.get(code)
        if canonical and canonical.get("shareDirection1d"):
            row["shareDirection1d"] = canonical["shareDirection1d"]
            if canonical.get("shareDelta1d") == 0.0:
                if _finite(row.get("shareDelta1d")) and float(row.get("shareDelta1d")) != 0:
                    row.setdefault("shareDelta1dRaw", float(row["shareDelta1d"]))
                row["shareDelta1d"] = 0.0
                for key in ("flow1d", "primaryFlow1d"):
                    if _finite(row.get(key)) and float(row.get(key)) != 0:
                        row.setdefault(f"{key}Raw", float(row[key]))
                        row[key] = 0.0
        else:
            _set_direction_fields(row)

    flow_model_v2._recalculate_groups(snapshot)
    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    scopes = primary.setdefault("scopeTotals", {})
    for scope in ("allEtf", "stockEtfIncludingCrossBorder", "aShareStockEtf"):
        scopes[scope] = _reconcile_scope_row(scopes.get(scope, {}), _scope_rows(snapshot, scope))

    assets = primary.setdefault("assetClassTotals", {})
    for scope in ASSET_SCOPES:
        assets[scope] = _reconcile_scope_row(assets.get(scope, {}), _scope_rows(snapshot, scope))

    all_flow = round(sum(float(assets[s]["flow1d"]) for s in ASSET_SCOPES), 2)
    primary["assetClassReconciliation"] = {
        "sumOfMutuallyExclusiveAssetClasses": all_flow,
        "allEtfScopeTotal": scopes["allEtf"]["flow1d"],
        "difference": round(all_flow - float(scopes["allEtf"]["flow1d"]), 2),
    }

    market = snapshot.setdefault("market", {})
    endpoint5 = market.get("flow5dEndpoint", market.get("flow5d"))
    endpoint20 = market.get("flow20dEndpoint", market.get("flow20d"))
    market.update(scopes["aShareStockEtf"])
    market["flow5dEndpoint"] = endpoint5
    market["flow20dEndpoint"] = endpoint20
    market["metric"] = "primaryMarketNetSubscriptionEstimate"
    market["valuation"] = "sameDayUnitNAV"
    market["scopeKey"] = "aShareStockEtf"
    market["directionToleranceShares"] = DIRECTION_EPS_SHARES


def _ambiguity_reason(row: dict[str, Any]) -> str | None:
    group_id = str(row.get("groupId") or "")
    guard = _AMBIGUITY_GUARDS.get(group_id)
    if not guard:
        return None
    trigger, required, reason = guard
    text = str(row.get("name") or "")
    return reason if trigger.search(text) and not required.search(text) else None


def sanitize_classification(snapshot: dict[str, Any]) -> None:
    """Keep market coverage complete while making client groups conservative."""
    ambiguous: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    universe_by_code = {str(r.get("code", "")).zfill(6): r for r in snapshot.get("universe", [])}

    for row in snapshot.get("etfs", []):
        reason = _ambiguity_reason(row)
        code = str(row.get("code", "")).zfill(6)
        if reason:
            ambiguous.append({
                "code": code,
                "name": str(row.get("name") or ""),
                "previousGroupId": str(row.get("groupId") or ""),
                "reason": reason,
            })
            u = universe_by_code.get(code)
            if u is not None:
                u["classificationStatus"] = "ambiguous"
                u["classificationReason"] = reason
            continue
        row["classificationMethod"] = "fund_name_rule"
        row["classificationConfidence"] = "rule_based"
        kept.append(row)

    snapshot["etfs"] = kept
    member_groups = {str(r.get("groupId") or "") for r in kept if r.get("groupId")}
    snapshot["groups"] = [g for g in snapshot.get("groups", []) if str(g.get("id") or "") in member_groups]
    flow_model_v2._recalculate_groups(snapshot)

    for group in snapshot.get("groups", []):
        if group.get("kind") == "broad":
            layer = "broad_index_research_group"
        elif group.get("kind") == "style":
            layer = "style_research_group"
        elif group.get("parent"):
            layer = "theme_research_group"
        else:
            layer = "industry_research_group"
        group["classificationLayer"] = layer
        group["classificationMethod"] = "fund_name_rule"
        group["classificationClaim"] = "研究分组，不代表基金管理人或指数公司官方分类"

    market_count = max(int(snapshot.get("market", {}).get("etfCount") or 0), 1)
    quality = snapshot.setdefault("quality", {})
    quality["ambiguousClassificationCount"] = len(ambiguous)
    quality["ambiguousClassificationSample"] = ambiguous[:20]
    quality["classifiedEtfCount"] = len(kept)
    quality["classifiedCoverageOfMarketPct"] = round(len(kept) / market_count * 100, 2)
    quality["classificationContract"] = "conservative_name_rule_with_ambiguity_exclusion"


def _daily_fact(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    if data.get("metric") != "primaryMarketNetSubscriptionEstimate" or data.get("valuation") != "sameDayUnitNAV":
        return None
    return data


def _daily_group_map(data: dict[str, Any]) -> dict[str, float]:
    grouped: dict[str, float] = {}
    for row in data.get("etfs", []):
        gid = str(row.get("groupId") or "")
        value = row.get("flow1d")
        if gid and _finite(value):
            grouped[gid] = grouped.get(gid, 0.0) + float(value)
    return grouped


def _cumulative_market_value(snapshot: dict[str, Any], dates: list[str]) -> tuple[float | None, int]:
    total = 0.0
    count = 0
    for stamp in dates:
        if stamp == snapshot.get("tradeDate"):
            value = snapshot.get("market", {}).get("flow1d")
        else:
            fact = _daily_fact(base.PUBLIC / "daily" / f"{stamp}.json")
            value = None if fact is None else fact.get("marketScopes", {}).get("aShareStockEtf", {}).get("flow1d")
        if not _finite(value):
            return None, count
        total += float(value)
        count += 1
    return round(total, 2), count


def _cumulative_group_values(snapshot: dict[str, Any], dates: list[str]) -> tuple[dict[str, float] | None, int]:
    accum: dict[str, float] = {}
    count = 0
    current_map = {str(g.get("id")): float(g.get("flow1d") or 0) for g in snapshot.get("groups", [])}
    for stamp in dates:
        if stamp == snapshot.get("tradeDate"):
            group_map = current_map
        else:
            fact = _daily_fact(base.PUBLIC / "daily" / f"{stamp}.json")
            # Older facts can reconcile the market, but group history is not used
            # unless it was generated under the same classification contract.
            if fact is None or fact.get("dataContractVersion") != CONTRACT_VERSION:
                return None, count
            group_map = _daily_group_map(fact)
        for gid in current_map:
            accum[gid] = accum.get(gid, 0.0) + float(group_map.get(gid, 0.0))
        count += 1
    return {gid: round(value, 2) for gid, value in accum.items()}, count


def apply_cumulative_contract(snapshot: dict[str, Any], share_window: list[tuple[date, Any]]) -> None:
    """Publish true daily sums only when every required official session exists."""
    session_dates = [d.isoformat() for d, _ in share_window]
    market = snapshot.setdefault("market", {})
    market["flow5dEndpoint"] = market.get("flow5dEndpoint", market.get("flow5d"))
    market["flow20dEndpoint"] = market.get("flow20dEndpoint", market.get("flow20d"))
    market["flow5dEndpointMetric"] = "endpointShareChangeTimesCurrentNAV"
    market["flow20dEndpointMetric"] = "endpointShareChangeTimesCurrentNAV"

    for horizon in (5, 20):
        field = f"flow{horizon}d"
        cumulative_field = f"flow{horizon}dCumulative"
        status_field = f"flow{horizon}dCumulativeStatus"
        dates = session_dates[-horizon:] if len(session_dates) >= horizon else []
        value, observed = _cumulative_market_value(snapshot, dates) if dates else (None, 0)
        available = value is not None and observed == horizon
        market[cumulative_field] = value if available else None
        market[status_field] = "available" if available else "insufficient_verified_daily_history"
        market[field] = value if available else None

        group_values, group_observed = _cumulative_group_values(snapshot, dates) if dates else (None, 0)
        for group in snapshot.get("groups", []):
            endpoint_key = f"flow{horizon}dEndpoint"
            group[endpoint_key] = group.get(endpoint_key, group.get(field))
            group[f"flow{horizon}dEndpointMetric"] = "endpointShareChangeTimesCurrentNAV"
            if group_values is not None and group_observed == horizon:
                group[field] = group_values.get(str(group.get("id")), 0.0)
                group[f"flow{horizon}dMetric"] = "sumOfVerifiedDailyPrimaryFlows"
                group[f"flow{horizon}dCumulativeStatus"] = "available"
            else:
                group[field] = None
                group[f"flow{horizon}dMetric"] = "unavailableUntilVerifiedDailyHistory"
                group[f"flow{horizon}dCumulativeStatus"] = "insufficient_verified_daily_history"

    # The existing 5d intensity is explicitly an endpoint-intensity measure.
    for group in snapshot.get("groups", []):
        if _finite(group.get("flowIntensity5dPct")):
            group["flowIntensity5dEndpointPct"] = group["flowIntensity5dPct"]
        if _finite(group.get("flowIntensity20dPct")):
            group["flowIntensity20dEndpointPct"] = group["flowIntensity20dPct"]

    snapshot.setdefault("quality", {})["cumulativeFlowHistory"] = {
        "officialSessionDates": session_dates,
        "fiveDayStatus": market.get("flow5dCumulativeStatus"),
        "twentyDayStatus": market.get("flow20dCumulativeStatus"),
    }


def harmonize_secondary_metrics(snapshot: dict[str, Any]) -> None:
    metrics = snapshot.setdefault("flowMetrics", {})
    trade = metrics.get("secondaryMarketTradeFlow")
    if isinstance(trade, dict):
        trade["metric"] = "secondaryMarketAggressorImbalanceEstimate"
        trade["displayName"] = "主动成交方向差额（估算）"
        trade["definition"] = (
            "使用同日ETF成交额并按外盘/内盘主动成交量占比拆分，计算主动买入估算额减主动卖出估算额；"
            "这是成交方向启发式指标，不代表市场净新增资金，也不是ETF一级市场申购赎回。"
        )
        trade["source"] = "东方财富ETF同日行情：成交额、外盘、内盘"
        for row in trade.get("scopeTotals", {}).values():
            if "netFlow1d" in row:
                row["aggressorImbalance1d"] = row.get("netFlow1d")
                row["buyInitiatedEstimate1d"] = row.get("inflow1d")
                row["sellInitiatedEstimate1d"] = row.get("outflow1d")

    vendor = metrics.get("secondaryMarketOrderFlow")
    if isinstance(vendor, dict):
        vendor["displayName"] = "行情商“主力净额”字段（交易统计）"
        vendor["definition"] = (
            "东方财富行情体系提供的“主力净流入-净额”字段；其订单分档方法属于行情商交易统计，"
            "不等于ETF一级市场净申购/赎回，也不用于校准一级市场数据。"
        )


def rebuild_client_reconciliation(snapshot: dict[str, Any]) -> None:
    rollups = production._build_industry_rollups(snapshot)
    snapshot["industryRollups"] = rollups
    snapshot["themeGroups"] = [g for g in snapshot.get("groups", []) if g.get("kind") == "industry" and g.get("parent")]

    market = snapshot.get("market", {})
    groups = snapshot.get("groups", [])
    classified_flow = round(sum(float(g.get("flow1d") or 0) for g in groups), 2)
    visible_sectors = [g for g in groups if g.get("kind") == "industry"]
    visible_sector_flow = round(sum(float(g.get("flow1d") or 0) for g in visible_sectors), 2)
    rollup_flow = round(sum(float(g.get("flow1d") or 0) for g in rollups), 2)
    quality = snapshot.setdefault("quality", {})
    quality["marketScopeReconciliation"] = {
        "aShareEquityShareFlow1d": market.get("flow1d"),
        "classifiedGroupShareFlow1d": classified_flow,
        "ungroupedDifference": round(float(market.get("flow1d") or 0) - classified_flow, 2),
    }
    quality["clientSectorReconciliation"] = {
        "visibleGroupCount": len(visible_sectors),
        "visibleGroupFlow1d": visible_sector_flow,
        "industryRollupFlow1d": rollup_flow,
        "difference": round(visible_sector_flow - rollup_flow, 2),
        "displayLayer": "conservative_industry_and_theme_research_groups",
    }


def rebuild_conclusion(snapshot: dict[str, Any]) -> None:
    market = snapshot.get("market", {})
    value = float(market.get("flow1d") or 0)
    groups = snapshot.get("groups", [])
    broad = [g for g in groups if g.get("kind") == "broad"]
    sectors = [g for g in groups if g.get("kind") == "industry"]

    broad_in = sum(float(g.get("flow1d") or 0) > 0 for g in broad)
    broad_out = sum(float(g.get("flow1d") or 0) < 0 for g in broad)
    headline = (
        f"A股股票ETF按交易所日终份额变化估算当日{_flow_word(value)}；"
        f"统计范围{int(market.get('etfCount') or 0)}只。宽基研究组中{broad_out}个净赎回、{broad_in}个净申购。"
    )

    facts: list[str] = []
    if broad:
        out = sorted(broad, key=lambda g: float(g.get("flow1d") or 0))
        facts.append(
            "宽基净赎回估算居前为" + "、".join(
                f"{g['name']}{abs(float(g.get('flow1d') or 0)):.1f}亿元" for g in out[:3] if float(g.get("flow1d") or 0) < 0
            ) + "。"
        )
    if sectors:
        ordered = sorted(sectors, key=lambda g: float(g.get("flow1d") or 0), reverse=True)
        positive = [g for g in ordered if float(g.get("flow1d") or 0) > 0]
        negative = sorted(sectors, key=lambda g: float(g.get("flow1d") or 0))
        if positive:
            facts.append(
                "行业/主题研究分组净申购估算居前为" + "、".join(
                    f"{g['name']}{float(g.get('flow1d') or 0):+.1f}亿元" for g in positive[:2]
                ) + f"；净赎回估算最多为{negative[0]['name']}{abs(float(negative[0].get('flow1d') or 0)):.1f}亿元。"
            )
        else:
            facts.append(f"行业/主题研究分组当日均未录得净申购估算；净赎回估算最多为{negative[0]['name']}{abs(float(negative[0].get('flow1d') or 0)):.1f}亿元。")

    top_in = market.get("topInflowEtf") or {}
    top_out = market.get("topOutflowEtf") or {}
    if top_in and top_out:
        facts.append(
            f"单只ETF中，{top_in.get('name')}净申购估算{abs(float(top_in.get('flow1d') or 0)):.1f}亿元；"
            f"{top_out.get('name')}净赎回估算{abs(float(top_out.get('flow1d') or 0)):.1f}亿元。"
        )

    coverage = float(snapshot.get("quality", {}).get("classifiedCoverageOfMarketPct") or 0)
    snapshot["conclusion"] = {
        "headline": headline,
        "facts": facts,
        "interpretation": (
            "上述结论只描述ETF一级市场份额申购/赎回的金额估算及研究分组分布；"
            "不等同于二级市场成交资金，不直接代表投资者最终持仓意图或未来价格方向。"
        ),
        "confidence": "A" if coverage >= 95 else "B",
        "confidenceNote": (
            f"交易所日终份额与精确交易日NAV已通过发布校验；研究分组覆盖A股股票ETF的{coverage:.2f}%，"
            "歧义名称不进入分组结论。"
        ),
    }


def apply_wording_and_provenance(snapshot: dict[str, Any]) -> None:
    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["displayName"] = "ETF一级市场净申购/赎回金额估算"
    primary["definition"] = "（T日交易所日终份额－T-1日公司行动调整后的可比份额）×T日单位净值。"
    primary["valuation"] = "sameDayUnitNAV"
    primary["economicMeaning"] = "一级市场份额净申购/赎回的金额估算"

    snapshot["dataContractVersion"] = CONTRACT_VERSION
    snapshot.setdefault("quality", {})["dataContractVersion"] = CONTRACT_VERSION
    snapshot["provenance"] = {
        "primaryShares": {
            "source": "上海证券交易所/深圳证券交易所ETF日终份额",
            "role": "canonical",
            "timing": "交易日清算后份额",
        },
        "navAndFundType": {
            "source": "同花顺ETF精确交易日净值与基金类型（AKShare适配）",
            "role": "NAV估值与资产范围识别",
            "datePolicy": "必须与tradeDate完全一致",
        },
        "averagePriceComparison": {
            "source": "东方财富ETF同日行情（AKShare适配）",
            "role": "成交均价估值对照，不覆盖主口径",
        },
        "secondaryTrading": {
            "source": "东方财富ETF同日行情",
            "role": "独立成交方向统计，不覆盖一级市场口径",
        },
    }

    snapshot.setdefault("methodology", {}).update({
        "identity": "本站核心指标是ETF一级市场净申购/赎回金额估算。二级市场成交方向指标与一级市场申赎是不同经济变量，禁止互相替代、校准或据此推断投资者最终意图。",
        "flow": "当日净申购/赎回金额估算 =（T日交易所清算后份额 − T-1日公司行动调整后的可比份额）× T日单位净值；金额单位为亿元。",
        "direction": f"份额方向统一由shareDirection1d给出；绝对份额差小于{DIRECTION_EPS_SHARES}份视为浮点舍入噪声并记为不变，所有市场、资产类别和分组只数共用该字段。",
        "multiDay": "5日/20日“逐日累计净申赎”只有在对应官方交易日的每日一级市场flow1d事实全部存在时才发布；否则显示数据积累中。端点指标另行保留，定义为（期末份额−期初可比份额）×期末NAV，绝不称为累计资金流。",
        "classification": "宽基、风格、行业和主题为资金轮动研究分组。当前分类主要依据基金名称规则；存在泛化关键词或无法唯一判断暴露方向的ETF标记为ambiguous并从分组结论剔除，但仍保留在A股股票ETF市场总量中。研究分组不等于基金管理人或指数公司官方分类。",
        "sectorDisplay": "客户端统一称为“行业/主题研究分组”；不把名称规则映射包装成严格的申万一级行业官方分类。",
        "coordinates": "横轴为各研究组代表ETF近20日相对沪深300收益；纵轴为5日端点份额变化金额估算占5日前参考规模的比例。该图是代表ETF价格代理×端点份额变化，不是指数收益×累计资金流。",
        "secondary": "主动成交方向差额使用同日成交额与外盘/内盘比例估算，仅反映成交主动方向；行情商“主力净额”字段作为另一项独立交易统计保存。两者均不是ETF一级市场净申购/赎回。",
        "scope": "首页一级市场主指标固定为A股股票ETF；跨境股票ETF、债券ETF、货币ETF、商品ETF和其他ETF均保留在互斥资产类别审计中，但不混入A股股票ETF主指标。",
        "valuation": "主口径统一使用同日单位净值；同一份额变化按成交均价估值只作为外部口径对照，不改变主口径。",
    })


def apply_system_contract(
    snapshot: dict[str, Any],
    day: date,
    share_window: list[tuple[date, Any]],
) -> None:
    """Apply the complete client-facing contract after schema-v6 base facts exist."""
    canonicalize_directions_and_totals(snapshot)
    sanitize_classification(snapshot)
    # Classification filtering changes group totals but never market totals.
    flow_model_v2._recalculate_groups(snapshot)
    apply_cumulative_contract(snapshot, share_window)
    harmonize_secondary_metrics(snapshot)
    rebuild_client_reconciliation(snapshot)
    rebuild_conclusion(snapshot)
    apply_wording_and_provenance(snapshot)
