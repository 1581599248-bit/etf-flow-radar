"""Build mutually exclusive ETF asset-class totals and harden A-share groups.

Public iFinD summaries commonly split all-ETF flow into domestic stock, cross-
border, bond, money, commodity and other buckets. These totals are derived from
the same canonical share delta and NAV facts as the all-ETF total. Client-facing
broad/style/industry groups are stricter: they may contain only domestic A-share
stock ETFs, even when a cross-border ETF name happens to match a theme rule.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

import flow_model_v2
import update_daily as base


_LABELS = {
    "aShareStockEtf": "A股股票ETF",
    "crossBorderStockEtf": "跨境ETF",
    "bondEtf": "债券ETF",
    "moneyEtf": "货币ETF",
    "commodityEtf": "商品ETF",
    "otherEtf": "其他ETF",
}


def _row_flow(row: dict[str, Any]) -> float | None:
    # Do not sum the 2-decimal ETF display amount: across ~1,500 ETFs that creates
    # a visible rounding residual. Rebuild from the canonical share delta and NAV.
    delta = row.get("shareDelta1d")
    nav = row.get("nav")
    if isinstance(delta, (int, float)) and isinstance(nav, (int, float)) and nav > 0:
        return float(delta) * float(nav) / 1e8
    return None


def _enforce_a_share_group_scope(snapshot: dict[str, Any]) -> None:
    """Remove non-A-share ETFs from every client-facing classification group.

    The exchange universe intentionally retains all ETF asset classes for audit
    totals.  Classification names alone are not sufficient to define A-share
    exposure: an沪深-listed cross-border ETF can still contain words such as
    游戏、医药 or 芯片.  The authoritative assetScope therefore gates every
    broad/style/industry group after the v2 asset classifier has run.
    """
    scope_by_code = {
        str(row.get("code", "")).zfill(6): str(row.get("assetScope") or "otherEtf")
        for row in snapshot.get("universe", [])
    }
    original = list(snapshot.get("etfs", []))
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for row in original:
        code = str(row.get("code", "")).zfill(6)
        asset_scope = scope_by_code.get(code, "otherEtf")
        if asset_scope == "aShareStockEtf":
            row["assetScope"] = asset_scope
            kept.append(row)
        else:
            excluded.append({
                "code": code,
                "name": str(row.get("name") or ""),
                "groupId": str(row.get("groupId") or ""),
                "assetScope": asset_scope,
            })

    snapshot["etfs"] = kept
    member_ids = {str(row.get("groupId") or "") for row in kept if row.get("groupId")}
    snapshot["groups"] = [
        group for group in snapshot.get("groups", []) if str(group.get("id") or "") in member_ids
    ]
    flow_model_v2._recalculate_groups(snapshot)

    members_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in kept:
        members_by_group.setdefault(str(row.get("groupId") or ""), []).append(row)

    replacement_reps: list[dict[str, str]] = []
    replacement_groups: dict[str, dict[str, Any]] = {}
    for group in snapshot.get("groups", []):
        gid = str(group.get("id") or "")
        members = members_by_group.get(gid, [])
        member_codes = {str(row.get("code", "")).zfill(6) for row in members}
        rep = group.get("representative") or {}
        rep_code = str(rep.get("code") or "").zfill(6)
        if rep_code in member_codes:
            continue
        proxy = max(members, key=lambda row: float(row.get("aum") or 0))
        new_rep = {
            "group_id": gid,
            "code": str(proxy.get("code", "")).zfill(6),
            "name": str(proxy.get("name") or ""),
            "exchange": str(proxy.get("exchange") or ""),
        }
        group["representative"] = {"code": new_rep["code"], "name": new_rep["name"]}
        # Never retain a return series sourced from a now-excluded cross-border representative.
        group["return1d"] = None
        group["return5d"] = None
        group["return20d"] = None
        group["relativeReturn20d"] = None
        group["priceFlowState"] = "待补充"
        replacement_reps.append(new_rep)
        replacement_groups[gid] = group

    return_refresh_warning = None
    if replacement_reps:
        try:
            trade_day = date.fromisoformat(str(snapshot.get("tradeDate")))
            raw_start = snapshot.get("windowStartDate")
            start_day = (
                date.fromisoformat(str(raw_start)) - timedelta(days=7)
                if raw_start else trade_day - timedelta(days=45)
            )
            return_series = base.fetch_return_series(replacement_reps, start_day, trade_day)
            benchmark = next((g for g in snapshot.get("groups", []) if g.get("id") == "hs300"), None)
            benchmark20 = benchmark.get("return20d") if benchmark else None
            for gid, group in replacement_groups.items():
                frame = return_series.get(gid)
                if frame is None:
                    continue
                ret1 = base._pct_return(frame, 1)
                ret5 = base._pct_return(frame, 5)
                ret20 = base._pct_return(frame, 20)
                relative20 = (
                    round(float(ret20) - float(benchmark20), 2)
                    if isinstance(ret20, (int, float)) and isinstance(benchmark20, (int, float)) else None
                )
                group["return1d"] = ret1
                group["return5d"] = ret5
                group["return20d"] = ret20
                group["relativeReturn20d"] = relative20
                if isinstance(relative20, (int, float)):
                    group["priceFlowState"] = base._flow_state(
                        float(relative20), float(group.get("flowIntensity5dPct") or 0)
                    )
        except Exception as exc:  # flow facts remain valid; only the return proxy is unavailable.
            return_refresh_warning = str(exc)

    excluded_scopes = Counter(row["assetScope"] for row in excluded)
    quality = snapshot.setdefault("quality", {})
    quality["classifiedAshareScopeEnforcement"] = {
        "beforeCount": len(original),
        "afterCount": len(kept),
        "excludedCount": len(excluded),
        "excludedByScope": dict(sorted(excluded_scopes.items())),
        "excludedSample": excluded[:10],
        "returnRepresentativeRefreshCount": len(replacement_reps),
        "returnRepresentativeRefreshWarning": return_refresh_warning,
    }


def add_asset_class_totals(snapshot: dict[str, Any]) -> None:
    # First enforce the same domestic-A-share boundary used by snapshot.market.
    _enforce_a_share_group_scope(snapshot)

    buckets: dict[str, list[float]] = {key: [] for key in _LABELS}
    counts: dict[str, dict[str, int]] = {
        key: {"increase": 0, "decrease": 0, "unchanged": 0} for key in _LABELS
    }
    for row in snapshot.get("universe", []):
        scope = str(row.get("assetScope") or "otherEtf")
        if scope not in buckets:
            scope = "otherEtf"
        flow = _row_flow(row)
        delta = row.get("shareDelta1d")
        if flow is None or not isinstance(delta, (int, float)):
            continue
        buckets[scope].append(flow)
        if delta > 0:
            counts[scope]["increase"] += 1
        elif delta < 0:
            counts[scope]["decrease"] += 1
        else:
            counts[scope]["unchanged"] += 1

    raw_totals = {key: sum(values) for key, values in buckets.items()}
    totals: dict[str, dict[str, Any]] = {}
    for key, label in _LABELS.items():
        values = buckets[key]
        totals[key] = {
            "name": label,
            "etfCount": len(values),
            "flow1d": round(raw_totals[key], 2) if values else 0.0,
            "increaseEtfCount1d": counts[key]["increase"],
            "decreaseEtfCount1d": counts[key]["decrease"],
            "unchangedEtfCount1d": counts[key]["unchanged"],
        }

    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["assetClassTotals"] = totals
    all_total = round(sum(raw_totals.values()), 2)
    canonical_all = primary.get("scopeTotals", {}).get("allEtf", {}).get("flow1d")
    primary["assetClassReconciliation"] = {
        "sumOfMutuallyExclusiveAssetClasses": all_total,
        "allEtfScopeTotal": canonical_all,
        "difference": round(all_total - float(canonical_all), 2) if isinstance(canonical_all, (int, float)) else None,
    }
