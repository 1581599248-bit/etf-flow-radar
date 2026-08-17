"""Aggregate alternate valuation conventions for schema v6.

Primary-market *share change* is the economic quantity. Public datasets may
value that quantity with unit NAV (Choice-style) or average traded price
(Wind/StockStar-style). This module stores both aggregates side by side instead
of changing the underlying share delta or pretending one vendor convention is a
different cash-flow event.
"""
from __future__ import annotations

from typing import Any


def add_primary_valuation_comparisons(snapshot: dict[str, Any]) -> None:
    rows = snapshot.get("universe", [])
    scopes = {
        "allEtf": lambda r: True,
        "stockEtfIncludingCrossBorder": lambda r: r.get("assetScope") in {"aShareStockEtf", "crossBorderStockEtf"},
        "aShareStockEtf": lambda r: r.get("assetScope") == "aShareStockEtf",
    }
    comparison: dict[str, Any] = {
        "canonical": "sameDayUnitNAV",
        "alternatives": {
            "sameDayAverageTradedPrice": {
                "definition": "公司行动调整后的同一份额变化 × 当日成交均价/参考交易价；用于对照采用成交均价的Wind/资讯口径。",
                "scopeTotals": {},
            }
        },
    }
    target = comparison["alternatives"]["sameDayAverageTradedPrice"]["scopeTotals"]
    for key, predicate in scopes.items():
        values: list[float] = []
        for row in rows:
            if not predicate(row):
                continue
            delta = row.get("shareDelta1d")
            price = row.get("referencePrice")
            if not isinstance(delta, (int, float)) or not isinstance(price, (int, float)) or price <= 0:
                continue
            values.append(float(delta) * float(price) / 1e8)
        target[key] = {
            "etfCount": len(values),
            "flow1d": round(sum(values), 2) if values else None,
        }
    snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})["valuationComparisons"] = comparison
