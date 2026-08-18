"""Precision layer for Data Contract 7.0.

All monetary aggregates are rebuilt from the underlying formula in yuan and are
rounded only once at the final published aggregate. Display-rounded per-ETF
amounts are never used as aggregation inputs.
"""
from __future__ import annotations

import math
from typing import Any

import system_contract_v7 as contract
import update_daily_production as production


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _primary_yuan(row: dict[str, Any]) -> float | None:
    delta = row.get("shareDelta1d")
    nav = row.get("nav")
    if not _finite(delta) or not _finite(nav) or float(nav) <= 0:
        return None
    if row.get("shareDirection1d") == "unchanged":
        return 0.0
    return float(delta) * float(nav)


def _endpoint_yuan(row: dict[str, Any], horizon: int) -> float | None:
    delta = row.get(f"shareDelta{horizon}dEndpoint")
    nav = row.get("nav")
    if not _finite(delta) or not _finite(nav) or float(nav) <= 0:
        return None
    return float(delta) * float(nav)


def _aum_yuan(row: dict[str, Any]) -> float | None:
    shares = row.get("shares")
    nav = row.get("nav")
    if not _finite(shares) or not _finite(nav) or float(shares) < 0 or float(nav) <= 0:
        return None
    return float(shares) * float(nav)


def _prior_aum_yuan(row: dict[str, Any], horizon: int) -> float | None:
    shares = row.get(f"shares{horizon}dAgoComparable")
    nav = row.get("nav")
    if not _finite(shares) or not _finite(nav) or float(shares) < 0 or float(nav) <= 0:
        return None
    return float(shares) * float(nav)


def _scope_rows(snapshot: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    rows = [row for row in snapshot.get("universe", []) if _primary_yuan(row) is not None]
    if scope == "allEtf":
        return rows
    if scope == "stockEtfIncludingCrossBorder":
        return [row for row in rows if row.get("assetScope") in {"aShareStockEtf", "crossBorderStockEtf"}]
    return [row for row in rows if row.get("assetScope") == scope]


def _canonical_product(row: dict[str, Any], yuan: float) -> dict[str, Any]:
    return {
        "code": str(row.get("code", "")).zfill(6),
        "name": str(row.get("name") or ""),
        "amountYuanEstimate": round(float(yuan), 2),
        "amountYiEstimate": round(float(yuan) / 1e8, 4),
    }


def _aggregate_scope(rows: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    exact = [(row, _primary_yuan(row)) for row in rows]
    exact = [(row, float(yuan)) for row, yuan in exact if yuan is not None]
    total_yuan = sum(yuan for _, yuan in exact)
    aum_values = [_aum_yuan(row) for row, _ in exact]
    aum_yuan = sum(float(value) for value in aum_values if value is not None)
    counts = {
        "increase": sum(row.get("shareDirection1d") == "increase" for row, _ in exact),
        "decrease": sum(row.get("shareDirection1d") == "decrease" for row, _ in exact),
        "unchanged": sum(row.get("shareDirection1d") == "unchanged" for row, _ in exact),
    }
    positives = [(row, yuan) for row, yuan in exact if yuan > 0]
    negatives = [(row, yuan) for row, yuan in exact if yuan < 0]

    result.update({
        "etfCount": len(exact),
        "primaryFlow1dYuanEstimate": round(total_yuan, 2),
        "flow1d": round(total_yuan / 1e8, 2),
        "aum": round(aum_yuan / 1e8, 2),
        "increaseEtfCount1d": counts["increase"],
        "decreaseEtfCount1d": counts["decrease"],
        "unchangedEtfCount1d": counts["unchanged"],
        "unchangedEtfPct1d": round(counts["unchanged"] / len(exact) * 100, 2) if exact else 0.0,
        "breadth1d": round((counts["increase"] - counts["decrease"]) / len(exact) * 100, 1) if exact else 0.0,
        "aggregationMethod1d": "sum_unrounded_share_delta_times_same_day_nav_then_round",
    })
    result.pop("topInflowEtf", None)
    result.pop("topOutflowEtf", None)
    result["largestNetSubscriptionEtf"] = (
        _canonical_product(*max(positives, key=lambda item: item[1])) if positives else None
    )
    result["largestNetRedemptionEtf"] = (
        _canonical_product(*min(negatives, key=lambda item: item[1])) if negatives else None
    )
    return result


def reconcile_primary_precision(snapshot: dict[str, Any]) -> None:
    """Rebuild market and asset totals from unrounded formula facts."""
    universe_by_code = {
        str(row.get("code", "")).zfill(6): row for row in snapshot.get("universe", [])
    }
    for row in snapshot.get("universe", []):
        yuan = _primary_yuan(row)
        if yuan is not None:
            row["primaryFlow1dYuanEstimate"] = round(yuan, 2)
            row["primaryFlow1d"] = round(yuan / 1e8, 2)

    for row in snapshot.get("etfs", []):
        code = str(row.get("code", "")).zfill(6)
        source = universe_by_code.get(code, row)
        # Use the client row's NAV/delta when available; otherwise copy the same
        # canonical formula facts from the complete universe.
        if not _finite(row.get("shareDelta1d")) and source is not row:
            row["shareDelta1d"] = source.get("shareDelta1d")
        if not _finite(row.get("nav")) and source is not row:
            row["nav"] = source.get("nav")
        yuan = _primary_yuan(row)
        if yuan is not None:
            row["primaryFlow1dYuanEstimate"] = round(yuan, 2)
            row["primaryFlow1d"] = round(yuan / 1e8, 2)
            row["flow1d"] = round(yuan / 1e8, 2)
            row["flowMetric"] = "primaryMarketNetSubscriptionEstimate"
            row["flowValuation"] = "sameDayUnitNAV"

    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    scopes = primary.setdefault("scopeTotals", {})
    for scope in ("allEtf", "stockEtfIncludingCrossBorder", "aShareStockEtf"):
        scopes[scope] = _aggregate_scope(_scope_rows(snapshot, scope), scopes.get(scope, {}))

    assets = primary.setdefault("assetClassTotals", {})
    for scope in contract.ASSET_SCOPES:
        assets[scope] = _aggregate_scope(_scope_rows(snapshot, scope), assets.get(scope, {}))

    all_asset_yuan = sum(float(assets[key].get("primaryFlow1dYuanEstimate") or 0) for key in contract.ASSET_SCOPES)
    all_scope_yuan = float(scopes["allEtf"].get("primaryFlow1dYuanEstimate") or 0)
    primary["assetClassReconciliation"] = {
        "sumOfMutuallyExclusiveAssetClassesYuanEstimate": round(all_asset_yuan, 2),
        "allEtfScopeTotalYuanEstimate": round(all_scope_yuan, 2),
        "differenceYuanEstimate": round(all_asset_yuan - all_scope_yuan, 2),
        "difference": round((all_asset_yuan - all_scope_yuan) / 1e8, 8),
    }

    market = snapshot.setdefault("market", {})
    preserved = {
        key: market.get(key)
        for key in (
            "flow5d", "flow20d", "flow5dEndpoint", "flow20dEndpoint",
            "flow5dCumulative", "flow20dCumulative", "flow5dCumulativeStatus",
            "flow20dCumulativeStatus", "multiDayMethod", "endpointMethod",
        )
        if key in market
    }
    market.update(scopes["aShareStockEtf"])
    market.update(preserved)
    market["metric"] = "primaryMarketNetSubscriptionEstimate"
    market["valuation"] = "sameDayUnitNAV"
    market["scopeKey"] = "aShareStockEtf"
    market["scope"] = "domestic_a_share_stock_etf"


def reconcile_group_precision(snapshot: dict[str, Any]) -> None:
    """Aggregate client research groups from formula facts, never display-rounded amounts."""
    members: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("etfs", []):
        gid = str(row.get("groupId") or "")
        if gid:
            members.setdefault(gid, []).append(row)

    for group in snapshot.get("groups", []):
        rows = members.get(str(group.get("id") or ""), [])
        exact_primary = [(_primary_yuan(row), row) for row in rows]
        exact_primary = [(float(yuan), row) for yuan, row in exact_primary if yuan is not None]
        primary_yuan = sum(yuan for yuan, _ in exact_primary)
        aum_yuan = sum(float(value) for value in (_aum_yuan(row) for row in rows) if value is not None)
        group["primaryFlow1dYuanEstimate"] = round(primary_yuan, 2)
        group["flow1d"] = round(primary_yuan / 1e8, 2)
        group["aum"] = round(aum_yuan / 1e8, 2)
        group["etfCount"] = len(rows)
        group["aggregationMethod1d"] = "sum_unrounded_share_delta_times_same_day_nav_then_round"
        group["flowMetric"] = "primaryMarketNetSubscriptionEstimate"
        group["flowValuation"] = "sameDayUnitNAV"

        for horizon in (5, 20):
            sample = []
            for row in rows:
                endpoint_yuan = _endpoint_yuan(row, horizon)
                prior_yuan = _prior_aum_yuan(row, horizon)
                if endpoint_yuan is not None and prior_yuan is not None:
                    sample.append((row, float(endpoint_yuan), float(prior_yuan)))
            if sample:
                endpoint_yuan = sum(item[1] for item in sample)
                prior_aum_yuan = sum(item[2] for item in sample)
                group[f"flow{horizon}dEndpointYuanEstimate"] = round(endpoint_yuan, 2)
                group[f"flow{horizon}dEndpoint"] = round(endpoint_yuan / 1e8, 2)
                group[f"endpointSampleCount{horizon}d"] = len(sample)
                group[f"priorReferenceAum{horizon}d"] = round(prior_aum_yuan / 1e8, 2)
                group[f"flowIntensity{horizon}dEndpointPct"] = (
                    round(endpoint_yuan / prior_aum_yuan * 100, 4) if prior_aum_yuan > 0 else None
                )


def _refresh_rollups(snapshot: dict[str, Any]) -> None:
    """Make hidden parent research rollups inherit exact leaf-group aggregates."""
    rollups = production._build_industry_rollups(snapshot)
    group_map = {str(group.get("id")): group for group in snapshot.get("groups", [])}
    for rollup in rollups:
        leaves = [group_map[str(gid)] for gid in rollup.get("leafGroups", []) if str(gid) in group_map]
        if not leaves:
            continue
        exact_yuan = sum(float(group.get("primaryFlow1dYuanEstimate") or 0) for group in leaves)
        rollup["primaryFlow1dYuanEstimate"] = round(exact_yuan, 2)
        rollup["flow1d"] = round(exact_yuan / 1e8, 2)
        rollup["aum"] = round(sum(float(group.get("aum") or 0) for group in leaves), 2)
        rollup["etfCount"] = sum(int(group.get("etfCount") or 0) for group in leaves)
    snapshot["industryRollups"] = rollups


def rebuild_precision_reconciliation(snapshot: dict[str, Any]) -> None:
    groups = snapshot.get("groups", [])
    all_client_yuan = sum(float(row.get("primaryFlow1dYuanEstimate") or 0) for row in snapshot.get("etfs", []))
    market_yuan = float(snapshot.get("market", {}).get("primaryFlow1dYuanEstimate") or 0)
    sectors = [group for group in groups if group.get("kind") == "industry"]
    visible_sector_yuan = sum(float(group.get("primaryFlow1dYuanEstimate") or 0) for group in sectors)
    _refresh_rollups(snapshot)
    rollup_yuan = sum(float(group.get("primaryFlow1dYuanEstimate") or 0) for group in snapshot.get("industryRollups", []))
    quality = snapshot.setdefault("quality", {})
    quality["marketScopeReconciliation"] = {
        "aShareEquityPrimaryFlow1dYuanEstimate": round(market_yuan, 2),
        "classifiedGroupPrimaryFlow1dYuanEstimate": round(all_client_yuan, 2),
        "ungroupedDifferenceYuanEstimate": round(market_yuan - all_client_yuan, 2),
        "aShareEquityShareFlow1d": round(market_yuan / 1e8, 2),
        "classifiedGroupShareFlow1d": round(all_client_yuan / 1e8, 2),
        "ungroupedDifference": round((market_yuan - all_client_yuan) / 1e8, 2),
        "aggregationMethod": "unrounded_formula_yuan_before_final_rounding",
    }
    quality["clientSectorReconciliation"] = {
        "visibleGroupCount": len(sectors),
        "visibleGroupPrimaryFlow1dYuanEstimate": round(visible_sector_yuan, 2),
        "industryRollupPrimaryFlow1dYuanEstimate": round(rollup_yuan, 2),
        "differenceYuanEstimate": round(visible_sector_yuan - rollup_yuan, 2),
        "visibleGroupFlow1d": round(visible_sector_yuan / 1e8, 2),
        "industryRollupFlow1d": round(rollup_yuan / 1e8, 2),
        "difference": round((visible_sector_yuan - rollup_yuan) / 1e8, 8),
        "displayLayer": "conservative_industry_and_theme_research_groups",
    }
    quality["monetaryAggregationContract"] = "sum_formula_amounts_in_yuan_before_any_display_rounding"


def _amount_phrase(yuan: float, positive_word: str, negative_word: str) -> str:
    value = yuan / 1e8
    if yuan > 0:
        return f"{positive_word}{value:.1f}亿元"
    if yuan < 0:
        return f"{negative_word}{abs(value):.1f}亿元"
    return "净申购/赎回估算0.0亿元"


def rebuild_precise_conclusion(snapshot: dict[str, Any]) -> None:
    market = snapshot.get("market", {})
    market_yuan = float(market.get("primaryFlow1dYuanEstimate") or 0)
    groups = snapshot.get("groups", [])
    broad = [group for group in groups if group.get("kind") == "broad"]
    sectors = [group for group in groups if group.get("kind") == "industry"]
    broad_pos = [g for g in broad if float(g.get("primaryFlow1dYuanEstimate") or 0) > 0]
    broad_neg = [g for g in broad if float(g.get("primaryFlow1dYuanEstimate") or 0) < 0]
    broad_zero = len(broad) - len(broad_pos) - len(broad_neg)

    headline = (
        f"A股股票ETF按交易所日终份额变化估算当日"
        f"{_amount_phrase(market_yuan, '净申购', '净赎回')}；统计范围{int(market.get('etfCount') or 0)}只。"
        f"宽基研究组中{len(broad_pos)}个净申购、{len(broad_neg)}个净赎回"
        + (f"、{broad_zero}个估算金额为0" if broad_zero else "")
        + "。"
    )

    facts: list[str] = []
    if broad_neg:
        ordered = sorted(broad_neg, key=lambda g: float(g.get("primaryFlow1dYuanEstimate") or 0))
        facts.append(
            "宽基净赎回估算居前为" + "、".join(
                f"{g['name']}{abs(float(g.get('primaryFlow1dYuanEstimate') or 0))/1e8:.1f}亿元"
                for g in ordered[:3]
            ) + "。"
        )
    elif broad:
        facts.append("宽基研究组当日均未录得净赎回估算。")

    sector_pos = [g for g in sectors if float(g.get("primaryFlow1dYuanEstimate") or 0) > 0]
    sector_neg = [g for g in sectors if float(g.get("primaryFlow1dYuanEstimate") or 0) < 0]
    if sector_pos:
        ordered_pos = sorted(sector_pos, key=lambda g: float(g.get("primaryFlow1dYuanEstimate") or 0), reverse=True)
        text = "行业/主题研究分组净申购估算居前为" + "、".join(
            f"{g['name']}{float(g.get('primaryFlow1dYuanEstimate') or 0)/1e8:+.1f}亿元" for g in ordered_pos[:2]
        )
        if sector_neg:
            worst = min(sector_neg, key=lambda g: float(g.get("primaryFlow1dYuanEstimate") or 0))
            text += f"；净赎回估算最多为{worst['name']}{abs(float(worst.get('primaryFlow1dYuanEstimate') or 0))/1e8:.1f}亿元。"
        else:
            text += "；当日未录得净赎回研究分组。"
        facts.append(text)
    elif sectors:
        if sector_neg:
            worst = min(sector_neg, key=lambda g: float(g.get("primaryFlow1dYuanEstimate") or 0))
            facts.append(
                f"行业/主题研究分组当日均未录得净申购估算；净赎回估算最多为{worst['name']}"
                f"{abs(float(worst.get('primaryFlow1dYuanEstimate') or 0))/1e8:.1f}亿元。"
            )
        else:
            facts.append("行业/主题研究分组当日净申购/赎回估算均为0。")

    largest_sub = market.get("largestNetSubscriptionEtf")
    largest_red = market.get("largestNetRedemptionEtf")
    if largest_sub or largest_red:
        parts = []
        if largest_sub:
            parts.append(
                f"{largest_sub['name']}净申购估算{float(largest_sub['amountYuanEstimate'])/1e8:.1f}亿元"
            )
        if largest_red:
            parts.append(
                f"{largest_red['name']}净赎回估算{abs(float(largest_red['amountYuanEstimate']))/1e8:.1f}亿元"
            )
        facts.append("单只ETF中，" + "；".join(parts) + "。")

    coverage = float(snapshot.get("quality", {}).get("classifiedCoverageOfMarketPct") or 0)
    snapshot["conclusion"] = {
        "headline": headline,
        "facts": facts,
        "interpretation": (
            "上述结论只描述ETF一级市场份额净申购/赎回的金额估算及研究分组分布；"
            "不等同于二级市场成交资金，不直接代表投资者最终持仓意图或未来价格方向。"
        ),
        "confidence": "A" if coverage >= 95 else "B",
        "confidenceNote": (
            f"交易所日终份额与精确交易日NAV已通过生产校验；研究分组覆盖A股股票ETF的{coverage:.2f}%，"
            "歧义名称不进入分组结论。"
        ),
    }


def apply(snapshot: dict[str, Any]) -> None:
    reconcile_primary_precision(snapshot)
    reconcile_group_precision(snapshot)
    rebuild_precision_reconciliation(snapshot)
    rebuild_precise_conclusion(snapshot)
