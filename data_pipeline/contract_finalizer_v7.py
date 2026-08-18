"""Final deterministic normalization after the unified v7 contract.

The finalizer removes legacy ambiguous fields as well as reconciling endpoint
breadth, representatives and same-contract cumulative history. A Contract 7.0
snapshot must be safe to consume directly from JSON, not only through the UI.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import system_contract_v7 as contract
import update_daily as base

CLASSIFICATION_PATH = Path(__file__).with_name("classification.json")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _direction(delta: Any) -> str | None:
    if not _finite(delta):
        return None
    value = float(delta)
    if abs(value) < contract.DIRECTION_EPS_SHARES:
        return "unchanged"
    return "increase" if value > 0 else "decrease"


def _state(relative_return: Any, endpoint_intensity: Any) -> str:
    if not _finite(relative_return) or not _finite(endpoint_intensity):
        return "数据待补"
    relative = float(relative_return)
    intensity = float(endpoint_intensity)
    if relative >= 0 and intensity >= 0:
        return "跑赢基准 · 份额增加"
    if relative < 0 <= intensity:
        return "跑输基准 · 份额增加"
    if relative >= 0 > intensity:
        return "跑赢基准 · 份额减少"
    return "跑输基准 · 份额减少"


def _classification_digest() -> str:
    return hashlib.sha256(CLASSIFICATION_PATH.read_bytes()).hexdigest()


def _load_daily(stamp: str) -> dict[str, Any] | None:
    path = base.PUBLIC / "daily" / f"{stamp}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    if payload.get("dataContractVersion") != contract.CONTRACT_VERSION:
        return None
    if payload.get("metric") != "primaryMarketNetSubscriptionEstimate":
        return None
    if payload.get("valuation") != "sameDayUnitNAV":
        return None
    return payload


def _daily_group_map(payload: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    for row in payload.get("etfs", []):
        gid = str(row.get("groupId") or "")
        value = row.get("flow1d")
        if gid and _finite(value):
            output[gid] = output.get(gid, 0.0) + float(value)
    return output


def _daily_etf_map(payload: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("code", "")).zfill(6): float(row["flow1d"])
        for row in payload.get("etfs", [])
        if _finite(row.get("flow1d"))
    }


def _enforce_strict_cumulative(snapshot: dict[str, Any], digest: str) -> None:
    """Never add daily facts created under different semantic/classification contracts."""
    quality = snapshot.setdefault("quality", {})
    official_dates = list((quality.get("cumulativeFlowHistory") or {}).get("officialSessionDates") or [])
    current_date = str(snapshot.get("tradeDate") or "")
    current_group_map = {
        str(group.get("id")): float(group.get("flow1d") or 0)
        for group in snapshot.get("groups", [])
    }
    current_etf_map = {
        str(row.get("code", "")).zfill(6): float(row.get("flow1d") or 0)
        for row in snapshot.get("etfs", [])
        if _finite(row.get("flow1d"))
    }
    market = snapshot.setdefault("market", {})

    for horizon in (5, 20):
        dates = official_dates[-horizon:] if len(official_dates) >= horizon else []
        market_values: list[float] = []
        group_accum = {gid: 0.0 for gid in current_group_map}
        etf_accum = {code: 0.0 for code in current_etf_map}
        market_ok = bool(dates)
        comparable_group_history = bool(dates)

        for stamp in dates:
            if stamp == current_date:
                market_value = market.get("flow1d")
                group_map = current_group_map
                etf_map = current_etf_map
                daily_digest = digest
            else:
                payload = _load_daily(stamp)
                if payload is None:
                    market_ok = False
                    comparable_group_history = False
                    break
                market_value = payload.get("marketScopes", {}).get("aShareStockEtf", {}).get("flow1d")
                group_map = _daily_group_map(payload)
                etf_map = _daily_etf_map(payload)
                daily_digest = str(payload.get("classificationRuleDigest") or "")

            if not _finite(market_value):
                market_ok = False
            else:
                market_values.append(float(market_value))

            if daily_digest != digest:
                comparable_group_history = False
            if comparable_group_history:
                for gid in group_accum:
                    group_accum[gid] += float(group_map.get(gid, 0.0))
                for code in etf_accum:
                    etf_accum[code] += float(etf_map.get(code, 0.0))

        market_available = market_ok and len(market_values) == horizon
        comparable_available = market_available and comparable_group_history
        market[f"flow{horizon}dCumulative"] = round(sum(market_values), 2) if market_available else None
        market[f"flow{horizon}d"] = market[f"flow{horizon}dCumulative"]
        market[f"flow{horizon}dCumulativeStatus"] = (
            "available" if market_available else "insufficient_same_contract_daily_history"
        )
        market[f"flow{horizon}dCumulativeSourceDates"] = dates if market_available else []

        for group in snapshot.get("groups", []):
            gid = str(group.get("id") or "")
            if comparable_available:
                group[f"flow{horizon}d"] = round(group_accum.get(gid, 0.0), 2)
                group[f"flow{horizon}dMetric"] = "sumOfSameContractSameClassificationVerifiedDailyPrimaryFlows"
                group[f"flow{horizon}dCumulativeStatus"] = "available"
                group[f"flow{horizon}dCumulativeSourceDates"] = dates
            else:
                group[f"flow{horizon}d"] = None
                group[f"flow{horizon}dMetric"] = "unavailableUntilSameContractSameClassificationDailyHistory"
                group[f"flow{horizon}dCumulativeStatus"] = "insufficient_same_contract_or_classification_history"
                group[f"flow{horizon}dCumulativeSourceDates"] = []

        for row in snapshot.get("etfs", []):
            code = str(row.get("code", "")).zfill(6)
            # Remove the old ambiguous field whose v6 meaning was endpoint flow.
            row.pop(f"flow{horizon}d", None)
            row[f"flow{horizon}dCumulative"] = round(etf_accum.get(code, 0.0), 2) if comparable_available else None
            row[f"flow{horizon}dCumulativeStatus"] = (
                "available" if comparable_available else "insufficient_same_contract_or_classification_history"
            )

    market["multiDayMethod"] = "sum_of_same_contract_verified_daily_primary_flows"
    market["endpointMethod"] = "endpoint_share_change_times_current_nav"
    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["multiDay"] = {
        "cumulative": {
            "fiveDayField": "flow5dCumulative",
            "twentyDayField": "flow20dCumulative",
            "method": "sumOfSameContractVerifiedDailyPrimaryFlows",
            "availability": "all required official sessions must exist under Data Contract 7.0",
        },
        "endpoint": {
            "fiveDayField": "flow5dEndpoint",
            "twentyDayField": "flow20dEndpoint",
            "method": "endpointShareChangeTimesCurrentNAV",
            "warning": "endpoint change is not a sum of daily primary-market flows",
        },
    }
    quality["cumulativeFlowHistory"] = {
        **(quality.get("cumulativeFlowHistory") or {}),
        "requiredDataContractVersion": contract.CONTRACT_VERSION,
        "classificationRuleDigest": digest,
        "fiveDayStatus": market.get("flow5dCumulativeStatus"),
        "twentyDayStatus": market.get("flow20dCumulativeStatus"),
    }


def _repair_representatives(snapshot: dict[str, Any], members: dict[str, list[dict[str, Any]]]) -> None:
    repaired: list[dict[str, str]] = []
    for group in snapshot.get("groups", []):
        gid = str(group.get("id") or "")
        rows = members.get(gid, [])
        if not rows:
            continue
        codes = {str(row.get("code", "")).zfill(6) for row in rows}
        representative = group.get("representative") or {}
        rep_code = str(representative.get("code") or "").zfill(6)
        if rep_code in codes:
            continue
        replacement = max(rows, key=lambda row: float(row.get("aum") or 0))
        group["representative"] = {
            "code": str(replacement.get("code", "")).zfill(6),
            "name": str(replacement.get("name") or ""),
        }
        for field in ("return1d", "return5d", "return20d", "relativeReturn20d"):
            group[field] = None
        repaired.append({
            "groupId": gid,
            "previousRepresentative": rep_code,
            "newRepresentative": str(replacement.get("code", "")).zfill(6),
        })
    snapshot.setdefault("quality", {})["classificationRepresentativeRepairs"] = repaired


def _normalize_ambiguous_universe(snapshot: dict[str, Any]) -> None:
    for row in snapshot.get("universe", []):
        if row.get("classificationStatus") != "ambiguous":
            continue
        if row.get("groupId"):
            row["candidateGroupId"] = row.get("groupId")
        if row.get("groupName"):
            row["candidateGroupName"] = row.get("groupName")
        for field in ("groupId", "groupName", "kind"):
            row.pop(field, None)


def _normalize_secondary_schema(snapshot: dict[str, Any]) -> None:
    metrics = snapshot.setdefault("flowMetrics", {})

    trade = metrics.pop("secondaryMarketTradeFlow", None)
    if isinstance(trade, dict):
        for scope in trade.get("scopeTotals", {}).values():
            if "netFlow1d" in scope:
                scope["aggressorImbalance1d"] = scope.pop("netFlow1d")
            if "inflow1d" in scope:
                scope["buyInitiatedEstimate1d"] = scope.pop("inflow1d")
            if "outflow1d" in scope:
                scope["sellInitiatedEstimate1d"] = scope.pop("outflow1d")
        metrics["secondaryMarketAggressorImbalance"] = trade

    vendor = metrics.pop("secondaryMarketOrderFlow", None)
    if isinstance(vendor, dict):
        for scope in vendor.get("scopeTotals", {}).values():
            if "flow1d" in scope:
                scope["vendorMainOrderNet1d"] = scope.pop("flow1d")
        metrics["secondaryMarketVendorMainOrder"] = vendor

    for row in snapshot.get("etfs", []):
        if "secondaryTradeNetFlow1d" in row:
            row["secondaryAggressorImbalance1d"] = row.pop("secondaryTradeNetFlow1d")
        if "secondaryMainOrderFlow1d" in row:
            row["secondaryVendorMainOrderNet1d"] = row.pop("secondaryMainOrderFlow1d")

    quality = snapshot.setdefault("quality", {})
    quality["metricSeparation"] = "primary_market_subscription_vs_secondary_trading_statistics"
    quality.pop("secondaryOrderFlowStatus", None)
    aggressor = metrics.get("secondaryMarketAggressorImbalance") or {}
    vendor_metric = metrics.get("secondaryMarketVendorMainOrder") or {}
    quality["secondaryAggressorImbalanceStatus"] = aggressor.get("status", "unavailable")
    quality["secondaryVendorMainOrderStatus"] = vendor_metric.get("status", "unavailable")


def _normalize_research_rollups(snapshot: dict[str, Any]) -> None:
    old = snapshot.pop("industryRollups", None)
    if isinstance(old, list):
        for row in old:
            row["classificationClaim"] = "研究汇总，不代表指数公司或申万官方ETF分类"
        snapshot["industryResearchRollups"] = old
    for row in snapshot.get("themeGroups", []):
        row["classificationClaim"] = "主题研究分组，不代表指数公司官方分类"

    reconciliation = snapshot.get("quality", {}).get("clientSectorReconciliation")
    if isinstance(reconciliation, dict):
        if "industryRollupFlow1d" in reconciliation:
            reconciliation["industryResearchRollupFlow1d"] = reconciliation.pop("industryRollupFlow1d")
        reconciliation["displayLayer"] = "conservative_industry_and_theme_research_groups"


def finalize(snapshot: dict[str, Any]) -> None:
    digest = _classification_digest()
    snapshot["classificationRuleDigest"] = digest
    snapshot.setdefault("quality", {})["classificationRuleDigest"] = digest

    _normalize_ambiguous_universe(snapshot)

    members: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("etfs", []):
        gid = str(row.get("groupId") or "")
        if gid:
            members.setdefault(gid, []).append(row)
        for horizon in (5, 20):
            delta_key = f"shareDelta{horizon}dEndpoint"
            direction_key = f"shareDirection{horizon}dEndpoint"
            direction = _direction(row.get(delta_key))
            if direction is None:
                continue
            row[direction_key] = direction
            if direction == "unchanged" and _finite(row.get(delta_key)) and float(row[delta_key]) != 0:
                row.setdefault(f"{delta_key}Raw", float(row[delta_key]))
                row[delta_key] = 0.0
                endpoint_flow = f"flow{horizon}dEndpoint"
                if _finite(row.get(endpoint_flow)) and float(row[endpoint_flow]) != 0:
                    row.setdefault(f"{endpoint_flow}Raw", float(row[endpoint_flow]))
                    row[endpoint_flow] = 0.0

    _repair_representatives(snapshot, members)

    for group in snapshot.get("groups", []):
        rows = members.get(str(group.get("id") or ""), [])
        for horizon in (5, 20):
            directions = [r.get(f"shareDirection{horizon}dEndpoint") for r in rows]
            valid = [x for x in directions if x in {"increase", "decrease", "unchanged"}]
            increase = sum(x == "increase" for x in valid)
            decrease = sum(x == "decrease" for x in valid)
            unchanged = sum(x == "unchanged" for x in valid)
            group[f"increaseEtfCount{horizon}dEndpoint"] = increase
            group[f"decreaseEtfCount{horizon}dEndpoint"] = decrease
            group[f"unchangedEtfCount{horizon}dEndpoint"] = unchanged
            # Legacy aliases are exact mirrors for the existing frontend helper;
            # the explicit Endpoint fields are the authoritative names.
            group[f"increaseEtfCount{horizon}d"] = increase
            group[f"decreaseEtfCount{horizon}d"] = decrease
            group[f"unchangedEtfCount{horizon}d"] = unchanged
            group[f"endpointBreadthSampleCount{horizon}d"] = len(valid)

            endpoint_key = f"flow{horizon}dEndpoint"
            endpoint_values = [float(r[endpoint_key]) for r in rows if _finite(r.get(endpoint_key))]
            if endpoint_values:
                group[endpoint_key] = round(sum(endpoint_values), 2)

        if "flowIntensity5dPct" in group and "flowIntensity5dEndpointPct" not in group:
            group["flowIntensity5dEndpointPct"] = group["flowIntensity5dPct"]
        group.pop("flowIntensity5dPct", None)
        group.pop("flowIntensity5dBps", None)
        if "flowIntensity20dPct" in group and "flowIntensity20dEndpointPct" not in group:
            group["flowIntensity20dEndpointPct"] = group["flowIntensity20dPct"]
        group.pop("flowIntensity20dPct", None)
        group.pop("flowIntensity20dBps", None)

        intensity = group.get("flowIntensity5dEndpointPct")
        group["priceFlowState"] = _state(group.get("relativeReturn20d"), intensity)
        group["priceFlowStateMetric"] = "representativeEtfRelativeReturn20d_vs_endpointShareChangeIntensity5d"

    _enforce_strict_cumulative(snapshot, digest)
    _normalize_secondary_schema(snapshot)
    _normalize_research_rollups(snapshot)

    quality = snapshot.setdefault("quality", {})
    quality["endpointDirectionToleranceShares"] = contract.DIRECTION_EPS_SHARES
    quality["priceFlowStateSemantics"] = "representative_etf_price_proxy_vs_endpoint_share_change"
