"""Final deterministic normalization after the unified v7 contract.

This module prevents legacy helper fields from re-introducing imprecise
breadth, stale representative-ETF returns or mixed-contract cumulative data.
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
    raw = CLASSIFICATION_PATH.read_bytes()
    return hashlib.sha256(raw).hexdigest()


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


def _enforce_strict_cumulative(snapshot: dict[str, Any], digest: str) -> None:
    """Never add daily facts created under different semantic/classification contracts."""
    quality = snapshot.setdefault("quality", {})
    official_dates = list((quality.get("cumulativeFlowHistory") or {}).get("officialSessionDates") or [])
    current_date = str(snapshot.get("tradeDate") or "")
    current_group_map = {
        str(group.get("id")): float(group.get("flow1d") or 0)
        for group in snapshot.get("groups", [])
    }
    market = snapshot.setdefault("market", {})

    for horizon in (5, 20):
        dates = official_dates[-horizon:] if len(official_dates) >= horizon else []
        market_values: list[float] = []
        group_accum = {gid: 0.0 for gid in current_group_map}
        market_ok = bool(dates)
        group_ok = bool(dates)

        for stamp in dates:
            if stamp == current_date:
                market_value = market.get("flow1d")
                group_map = current_group_map
                daily_digest = digest
            else:
                payload = _load_daily(stamp)
                if payload is None:
                    market_ok = False
                    group_ok = False
                    break
                market_value = payload.get("marketScopes", {}).get("aShareStockEtf", {}).get("flow1d")
                group_map = _daily_group_map(payload)
                daily_digest = str(payload.get("classificationRuleDigest") or "")

            if not _finite(market_value):
                market_ok = False
            else:
                market_values.append(float(market_value))

            if daily_digest != digest:
                group_ok = False
            if group_ok:
                for gid in group_accum:
                    group_accum[gid] += float(group_map.get(gid, 0.0))

        market_available = market_ok and len(market_values) == horizon
        market[f"flow{horizon}dCumulative"] = round(sum(market_values), 2) if market_available else None
        market[f"flow{horizon}d"] = market[f"flow{horizon}dCumulative"]
        market[f"flow{horizon}dCumulativeStatus"] = (
            "available" if market_available else "insufficient_same_contract_daily_history"
        )
        market[f"flow{horizon}dCumulativeSourceDates"] = dates if market_available else []

        for group in snapshot.get("groups", []):
            gid = str(group.get("id") or "")
            if market_available and group_ok:
                group[f"flow{horizon}d"] = round(group_accum.get(gid, 0.0), 2)
                group[f"flow{horizon}dMetric"] = "sumOfSameContractVerifiedDailyPrimaryFlows"
                group[f"flow{horizon}dCumulativeStatus"] = "available"
                group[f"flow{horizon}dCumulativeSourceDates"] = dates
            else:
                group[f"flow{horizon}d"] = None
                group[f"flow{horizon}dMetric"] = "unavailableUntilSameContractVerifiedDailyHistory"
                group[f"flow{horizon}dCumulativeStatus"] = "insufficient_same_contract_daily_history"
                group[f"flow{horizon}dCumulativeSourceDates"] = []

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
        # Return values belong to the previous representative and cannot be
        # silently carried over. A later production refresh may repopulate them.
        for field in ("return1d", "return5d", "return20d", "relativeReturn20d"):
            group[field] = None
        repaired.append({
            "groupId": gid,
            "previousRepresentative": rep_code,
            "newRepresentative": str(replacement.get("code", "")).zfill(6),
        })
    snapshot.setdefault("quality", {})["classificationRepresentativeRepairs"] = repaired


def finalize(snapshot: dict[str, Any]) -> None:
    digest = _classification_digest()
    snapshot["classificationRuleDigest"] = digest
    snapshot.setdefault("quality", {})["classificationRuleDigest"] = digest

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
            # Legacy aliases remain only as exact mirrors for existing UI code.
            group[f"increaseEtfCount{horizon}d"] = increase
            group[f"decreaseEtfCount{horizon}d"] = decrease
            group[f"unchangedEtfCount{horizon}d"] = unchanged
            group[f"endpointBreadthSampleCount{horizon}d"] = len(valid)

            endpoint_key = f"flow{horizon}dEndpoint"
            endpoint_values = [float(r[endpoint_key]) for r in rows if _finite(r.get(endpoint_key))]
            if endpoint_values:
                group[endpoint_key] = round(sum(endpoint_values), 2)

        intensity = group.get("flowIntensity5dEndpointPct", group.get("flowIntensity5dPct"))
        group["priceFlowState"] = _state(group.get("relativeReturn20d"), intensity)
        group["priceFlowStateMetric"] = "representativeEtfRelativeReturn20d_vs_endpointShareChangeIntensity5d"

    _enforce_strict_cumulative(snapshot, digest)

    quality = snapshot.setdefault("quality", {})
    quality["endpointDirectionToleranceShares"] = contract.DIRECTION_EPS_SHARES
    quality["priceFlowStateSemantics"] = "representative_etf_price_proxy_vs_endpoint_share_change"
