"""Final deterministic normalization after the unified v7 contract.

This module is deliberately small: it prevents legacy group helper fields from
re-introducing imprecise direction labels or float-noise breadth counts after
all economic facts have already been reconciled.
"""
from __future__ import annotations

import math
from typing import Any

import system_contract_v7 as contract


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


def finalize(snapshot: dict[str, Any]) -> None:
    members: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("etfs", []):
        gid = str(row.get("groupId") or "")
        if gid:
            members.setdefault(gid, []).append(row)
        for horizon in (5, 20):
            delta_key = f"shareDelta{horizon}dEndpoint"
            direction_key = f"shareDirection{horizon}dEndpoint"
            direction = _direction(row.get(delta_key))
            if direction is not None:
                row[direction_key] = direction
                if direction == "unchanged" and _finite(row.get(delta_key)) and float(row[delta_key]) != 0:
                    row.setdefault(f"{delta_key}Raw", float(row[delta_key]))
                    row[delta_key] = 0.0
                    endpoint_flow = f"flow{horizon}dEndpoint"
                    if _finite(row.get(endpoint_flow)) and float(row[endpoint_flow]) != 0:
                        row.setdefault(f"{endpoint_flow}Raw", float(row[endpoint_flow]))
                        row[endpoint_flow] = 0.0

    for group in snapshot.get("groups", []):
        rows = members.get(str(group.get("id") or ""), [])
        for horizon in (5, 20):
            directions = [r.get(f"shareDirection{horizon}dEndpoint") for r in rows]
            valid = [x for x in directions if x in {"increase", "decrease", "unchanged"}]
            group[f"increaseEtfCount{horizon}dEndpoint"] = sum(x == "increase" for x in valid)
            group[f"decreaseEtfCount{horizon}dEndpoint"] = sum(x == "decrease" for x in valid)
            group[f"unchangedEtfCount{horizon}dEndpoint"] = sum(x == "unchanged" for x in valid)
            group[f"endpointBreadthSampleCount{horizon}d"] = len(valid)

        intensity = group.get("flowIntensity5dEndpointPct", group.get("flowIntensity5dPct"))
        group["priceFlowState"] = _state(group.get("relativeReturn20d"), intensity)
        group["priceFlowStateMetric"] = "representativeEtfRelativeReturn20d_vs_endpointShareChangeIntensity5d"

    snapshot.setdefault("quality", {})["endpointDirectionToleranceShares"] = contract.DIRECTION_EPS_SHARES
    snapshot["quality"]["priceFlowStateSemantics"] = "representative_etf_price_proxy_vs_endpoint_share_change"
