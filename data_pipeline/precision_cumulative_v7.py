"""Exact-yuan multi-day cumulative primary-flow layer for Data Contract 7.0.

A multi-day cumulative amount is a sum of daily primary-market formula facts.
This module requires each daily fact to expose its unrounded yuan estimate and
sums those yuan values before converting to 亿元 exactly once. It never sums
per-day or per-ETF display-rounded 亿元 values.
"""
from __future__ import annotations

import json
import math
from typing import Any

import system_contract_v7 as contract
import update_daily as base


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _read_daily(stamp: str) -> dict[str, Any] | None:
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
    if payload.get("aggregationMethod1d") != "sum_unrounded_share_delta_times_same_day_nav_then_round":
        return None
    return payload


def _daily_etf_yuan_map(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in payload.get("etfs", []):
        code = str(row.get("code", "")).zfill(6)
        value = row.get("primaryFlow1dYuanEstimate")
        if code and _finite(value):
            result[code] = float(value)
    return result


def _daily_group_yuan_map(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in payload.get("etfs", []):
        gid = str(row.get("groupId") or "")
        value = row.get("primaryFlow1dYuanEstimate")
        if gid and _finite(value):
            result[gid] = result.get(gid, 0.0) + float(value)
    return result


def _current_etf_yuan_map(snapshot: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("code", "")).zfill(6): float(row["primaryFlow1dYuanEstimate"])
        for row in snapshot.get("etfs", [])
        if _finite(row.get("primaryFlow1dYuanEstimate"))
    }


def _current_group_yuan_map(snapshot: dict[str, Any]) -> dict[str, float]:
    return {
        str(group.get("id") or ""): float(group["primaryFlow1dYuanEstimate"])
        for group in snapshot.get("groups", [])
        if str(group.get("id") or "") and _finite(group.get("primaryFlow1dYuanEstimate"))
    }


def _official_dates(snapshot: dict[str, Any]) -> list[str]:
    quality = snapshot.get("quality", {})
    return list((quality.get("cumulativeFlowHistory") or {}).get("officialSessionDates") or [])


def _publish_amount(target: dict[str, Any], horizon: int, yuan: float | None, dates: list[str], status: str) -> None:
    target[f"flow{horizon}dCumulativeYuanEstimate"] = round(yuan, 2) if yuan is not None else None
    target[f"flow{horizon}dCumulative"] = round(yuan / 1e8, 2) if yuan is not None else None
    target[f"flow{horizon}d"] = target[f"flow{horizon}dCumulative"]
    target[f"flow{horizon}dCumulativeStatus"] = status
    target[f"flow{horizon}dCumulativeSourceDates"] = list(dates) if yuan is not None else []
    target[f"flow{horizon}dCumulativeAggregation"] = "sum_unrounded_daily_primary_flow_yuan_then_round"


def apply(snapshot: dict[str, Any]) -> None:
    dates_all = _official_dates(snapshot)
    current_date = str(snapshot.get("tradeDate") or "")
    digest = str(snapshot.get("classificationRuleDigest") or "")
    market = snapshot.setdefault("market", {})
    current_market_yuan = market.get("primaryFlow1dYuanEstimate")
    current_etfs = _current_etf_yuan_map(snapshot)
    current_groups = _current_group_yuan_map(snapshot)

    quality = snapshot.setdefault("quality", {})
    precision_status: dict[str, Any] = {
        "aggregation": "sum_unrounded_daily_primary_flow_yuan_then_round",
        "requiredDataContractVersion": contract.CONTRACT_VERSION,
        "classificationRuleDigest": digest,
    }

    for horizon in (5, 20):
        dates = dates_all[-horizon:] if len(dates_all) >= horizon else []
        market_values: list[float] = []
        etf_accum = {code: 0.0 for code in current_etfs}
        group_accum = {gid: 0.0 for gid in current_groups}
        market_ok = bool(dates)
        classified_ok = bool(dates)
        failure_reason = ""

        for stamp in dates:
            if stamp == current_date:
                if not _finite(current_market_yuan):
                    market_ok = False
                    classified_ok = False
                    failure_reason = "current market yuan fact missing"
                    break
                market_yuan = float(current_market_yuan)
                etf_map = current_etfs
                group_map = current_groups
                daily_digest = digest
            else:
                payload = _read_daily(stamp)
                if payload is None:
                    market_ok = False
                    classified_ok = False
                    failure_reason = f"{stamp} exact-yuan daily fact unavailable"
                    break
                market_yuan = payload.get("marketScopes", {}).get("aShareStockEtf", {}).get("primaryFlow1dYuanEstimate")
                if not _finite(market_yuan):
                    market_ok = False
                    classified_ok = False
                    failure_reason = f"{stamp} market yuan aggregate unavailable"
                    break
                market_yuan = float(market_yuan)
                etf_map = _daily_etf_yuan_map(payload)
                group_map = _daily_group_yuan_map(payload)
                daily_digest = str(payload.get("classificationRuleDigest") or "")

            market_values.append(float(market_yuan))
            if daily_digest != digest:
                classified_ok = False
                failure_reason = f"{stamp} classification rule digest differs"
            if classified_ok:
                for code in etf_accum:
                    etf_accum[code] += float(etf_map.get(code, 0.0))
                for gid in group_accum:
                    group_accum[gid] += float(group_map.get(gid, 0.0))

        market_available = market_ok and len(market_values) == horizon
        market_yuan_total = sum(market_values) if market_available else None
        _publish_amount(
            market,
            horizon,
            market_yuan_total,
            dates,
            "available" if market_available else "insufficient_exact_yuan_daily_history",
        )

        classified_available = market_available and classified_ok
        for row in snapshot.get("etfs", []):
            code = str(row.get("code", "")).zfill(6)
            yuan = etf_accum.get(code) if classified_available else None
            row[f"flow{horizon}dCumulativeYuanEstimate"] = round(yuan, 2) if yuan is not None else None
            row[f"flow{horizon}dCumulative"] = round(yuan / 1e8, 2) if yuan is not None else None
            row[f"flow{horizon}dCumulativeStatus"] = (
                "available" if classified_available else "insufficient_exact_yuan_same_classification_history"
            )
            row[f"flow{horizon}dCumulativeSourceDates"] = list(dates) if classified_available else []

        for group in snapshot.get("groups", []):
            gid = str(group.get("id") or "")
            yuan = group_accum.get(gid) if classified_available else None
            _publish_amount(
                group,
                horizon,
                yuan,
                dates,
                "available" if classified_available else "insufficient_exact_yuan_same_classification_history",
            )

        precision_status[f"{horizon}d"] = {
            "marketStatus": market.get(f"flow{horizon}dCumulativeStatus"),
            "classifiedStatus": (
                "available" if classified_available else "insufficient_exact_yuan_same_classification_history"
            ),
            "sourceDates": list(dates) if market_available else [],
            "failureReason": failure_reason or None,
        }

    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    multi = primary.setdefault("multiDay", {})
    multi["cumulative"] = {
        "fiveDayField": "flow5dCumulative",
        "twentyDayField": "flow20dCumulative",
        "fiveDayYuanField": "flow5dCumulativeYuanEstimate",
        "twentyDayYuanField": "flow20dCumulativeYuanEstimate",
        "method": "sumOfSameContractVerifiedDailyPrimaryFlows",
        "aggregation": "sumUnroundedDailyPrimaryFlowYuanThenRound",
        "availability": "all required official sessions must expose Contract-7 exact-yuan daily facts; group/ETF history must also share the current classification digest",
    }
    quality["precisionCumulativeFlow"] = precision_status
