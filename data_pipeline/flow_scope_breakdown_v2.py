"""Build mutually exclusive ETF asset-class totals for schema v6.

Public iFinD summaries commonly split all-ETF flow into domestic stock, cross-
border, bond, money, commodity and other buckets.  These totals are derived from
the same canonical primary-market per-ETF facts so they reconcile exactly to the
all-ETF total rather than being separately fetched headline numbers.
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
    value = row.get("primaryFlow1d")
    return float(value) if isinstance(value, (int, float)) else None


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

    totals: dict[str, dict[str, Any]] = {}
    for key, label in _LABELS.items():
        values = buckets[key]
        totals[key] = {
            "name": label,
            "etfCount": len(values),
            "flow1d": round(sum(values), 2) if values else 0.0,
            "increaseEtfCount1d": counts[key]["increase"],
            "decreaseEtfCount1d": counts[key]["decrease"],
            "unchangedEtfCount1d": counts[key]["unchanged"],
        }

    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["assetClassTotals"] = totals
    all_total = round(sum(row["flow1d"] for row in totals.values()), 2)
    canonical_all = primary.get("scopeTotals", {}).get("allEtf", {}).get("flow1d")
    primary["assetClassReconciliation"] = {
        "sumOfMutuallyExclusiveAssetClasses": all_total,
        "allEtfScopeTotal": canonical_all,
        "difference": round(all_total - float(canonical_all), 2) if isinstance(canonical_all, (int, float)) else None,
    }
