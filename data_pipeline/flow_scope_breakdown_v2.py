"""Build mutually exclusive ETF asset-class totals for schema v6.

Public iFinD summaries commonly split all-ETF flow into domestic stock, cross-
border, bond, money, commodity and other buckets. These totals are derived from
the same canonical share delta and NAV facts as the all-ETF total. Reconciliation
is performed before display rounding.
"""
from __future__ import annotations

from typing import Any


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


def add_asset_class_totals(snapshot: dict[str, Any]) -> None:
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
