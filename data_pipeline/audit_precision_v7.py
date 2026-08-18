"""Fail-closed precision audit for Data Contract 7.0.

This audit independently reconstructs one-day primary-market formula amounts in
yuan from every published `shareDelta1d × nav` fact. It proves that no market,
asset-class or research-group total was built by summing display-rounded
0.01亿元 ETF values.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import system_contract_v7 as contract

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "site" / "data" / "latest.json"
YUAN_TOL = 2.0


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _formula_yuan(row: dict[str, Any]) -> float | None:
    delta, nav = row.get("shareDelta1d"), row.get("nav")
    if not _finite(delta) or not _finite(nav) or float(nav) <= 0:
        return None
    if row.get("shareDirection1d") == "unchanged":
        return 0.0
    return float(delta) * float(nav)


def _endpoint_yuan(row: dict[str, Any], horizon: int) -> float | None:
    delta, nav = row.get(f"shareDelta{horizon}dEndpoint"), row.get("nav")
    if not _finite(delta) or not _finite(nav) or float(nav) <= 0:
        return None
    direction = row.get(f"shareDirection{horizon}dEndpoint")
    if direction == "unchanged":
        return 0.0
    return float(delta) * float(nav)


def _assert_yuan(actual: Any, expected: float, label: str, tol: float = YUAN_TOL) -> None:
    if not _finite(actual):
        raise AssertionError(f"{label} is not finite: {actual!r}")
    if abs(float(actual) - expected) > tol:
        raise AssertionError(f"{label} mismatch: stored={actual}, formula={expected:.2f}, tol={tol}")


def _scope(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    if key == "allEtf":
        return rows
    if key == "stockEtfIncludingCrossBorder":
        return [row for row in rows if row.get("assetScope") in {"aShareStockEtf", "crossBorderStockEtf"}]
    return [row for row in rows if row.get("assetScope") == key]


def audit(path: Path = SNAPSHOT) -> list[str]:
    snapshot = json.loads(path.read_text("utf-8"))
    checks: list[str] = []
    if snapshot.get("schemaVersion") != 7:
        raise AssertionError(f"client snapshot schemaVersion must be 7: {snapshot.get('schemaVersion')}")
    if snapshot.get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError("Data Contract 7.0 is not active")
    quality = snapshot.get("quality", {})
    if quality.get("monetaryAggregationContract") != "sum_formula_amounts_in_yuan_before_any_display_rounding":
        raise AssertionError("monetary aggregation contract marker missing")
    if quality.get("clientSnapshotSchemaVersion") != 7:
        raise AssertionError("client snapshot schema marker mismatch")
    checks.append("public schema and precision contract")

    universe = [row for row in snapshot.get("universe", []) if _formula_yuan(row) is not None]
    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    scopes = primary.get("scopeTotals", {})
    for key in ("allEtf", "stockEtfIncludingCrossBorder", "aShareStockEtf"):
        rows = _scope(universe, key)
        expected = sum(float(_formula_yuan(row) or 0) for row in rows)
        stored = scopes.get(key, {})
        _assert_yuan(stored.get("primaryFlow1dYuanEstimate"), expected, f"scope {key} yuan")
        expected_yi = round(expected / 1e8, 2)
        if stored.get("flow1d") != expected_yi:
            raise AssertionError(f"scope {key} final 亿元 rounding mismatch: {stored.get('flow1d')} vs {expected_yi}")
        if int(stored.get("etfCount") or 0) != len(rows):
            raise AssertionError(f"scope {key} ETF count mismatch")
    checks.append("market scopes aggregate unrounded formula yuan")

    market = snapshot.get("market", {})
    ashare = scopes.get("aShareStockEtf", {})
    _assert_yuan(market.get("primaryFlow1dYuanEstimate"), float(ashare.get("primaryFlow1dYuanEstimate")), "market vs A-share scope yuan", tol=0.01)
    if market.get("flow1d") != ashare.get("flow1d"):
        raise AssertionError("market display amount differs from canonical A-share scope")
    if "topInflowEtf" in market or "topOutflowEtf" in market:
        raise AssertionError("legacy sign-ambiguous market topInflow/topOutflow fields remain")
    largest_sub = market.get("largestNetSubscriptionEtf")
    largest_red = market.get("largestNetRedemptionEtf")
    ashare_rows = _scope(universe, "aShareStockEtf")
    positives = [(row, float(_formula_yuan(row))) for row in ashare_rows if float(_formula_yuan(row) or 0) > 0]
    negatives = [(row, float(_formula_yuan(row))) for row in ashare_rows if float(_formula_yuan(row) or 0) < 0]
    if positives:
        expected = max(positives, key=lambda item: item[1])
        if not largest_sub or str(largest_sub.get("code")) != str(expected[0].get("code")):
            raise AssertionError("largest net-subscription ETF is not the largest positive formula amount")
        _assert_yuan(largest_sub.get("amountYuanEstimate"), expected[1], "largest subscription yuan")
    elif largest_sub is not None:
        raise AssertionError("largestNetSubscriptionEtf must be null when no ETF has a positive amount")
    if negatives:
        expected = min(negatives, key=lambda item: item[1])
        if not largest_red or str(largest_red.get("code")) != str(expected[0].get("code")):
            raise AssertionError("largest net-redemption ETF is not the most negative formula amount")
        _assert_yuan(largest_red.get("amountYuanEstimate"), expected[1], "largest redemption yuan")
    elif largest_red is not None:
        raise AssertionError("largestNetRedemptionEtf must be null when no ETF has a negative amount")
    checks.append("market sign-aware extrema")

    assets = primary.get("assetClassTotals", {})
    for key in contract.ASSET_SCOPES:
        rows = _scope(universe, key)
        expected = sum(float(_formula_yuan(row) or 0) for row in rows)
        _assert_yuan(assets.get(key, {}).get("primaryFlow1dYuanEstimate"), expected, f"asset {key} yuan")
    recon = primary.get("assetClassReconciliation", {})
    _assert_yuan(recon.get("differenceYuanEstimate"), 0.0, "asset-class reconciliation", tol=0.02)
    checks.append("mutually exclusive asset classes aggregate unrounded formula yuan")

    members: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("etfs", []):
        members.setdefault(str(row.get("groupId") or ""), []).append(row)
        formula = _formula_yuan(row)
        if formula is not None:
            _assert_yuan(row.get("primaryFlow1dYuanEstimate"), formula, f"ETF {row.get('code')} primary yuan")

    for group in snapshot.get("groups", []):
        gid = str(group.get("id") or "")
        rows = members.get(gid, [])
        expected = sum(float(_formula_yuan(row) or 0) for row in rows)
        _assert_yuan(group.get("primaryFlow1dYuanEstimate"), expected, f"group {gid} primary yuan")
        if group.get("flow1d") != round(expected / 1e8, 2):
            raise AssertionError(f"group {gid} display rounding mismatch")
        if group.get("aggregationMethod1d") != "sum_unrounded_share_delta_times_same_day_nav_then_round":
            raise AssertionError(f"group {gid} aggregation method missing")
        for horizon in (5, 20):
            endpoint_values = [
                float(value) for value in (_endpoint_yuan(row, horizon) for row in rows) if value is not None
            ]
            stored_yuan = group.get(f"flow{horizon}dEndpointYuanEstimate")
            if endpoint_values and stored_yuan is not None:
                _assert_yuan(stored_yuan, sum(endpoint_values), f"group {gid} {horizon}d endpoint yuan")
    checks.append("research groups aggregate formula facts before display rounding")

    market_recon = quality.get("marketScopeReconciliation", {})
    classified_yuan = sum(float(row.get("primaryFlow1dYuanEstimate") or 0) for row in snapshot.get("etfs", []))
    market_yuan = float(market.get("primaryFlow1dYuanEstimate") or 0)
    _assert_yuan(market_recon.get("classifiedGroupPrimaryFlow1dYuanEstimate"), classified_yuan, "classified research-group yuan")
    _assert_yuan(market_recon.get("ungroupedDifferenceYuanEstimate"), market_yuan - classified_yuan, "ungrouped difference yuan")
    checks.append("market-to-research-group precision reconciliation")

    conclusion = snapshot.get("conclusion", {})
    text = " ".join([str(conclusion.get("headline") or ""), *map(str, conclusion.get("facts") or [])])
    for bad in ("净申购估算-", "净赎回估算-", "净流入+", "净流出-"):
        if bad in text:
            raise AssertionError(f"sign/wording contradiction in conclusion: {bad}")
    checks.append("conclusion sign wording")

    return checks


def main() -> int:
    try:
        checks = audit()
    except Exception as exc:
        print(f"PRECISION AUDIT FAILED: {exc}")
        return 1
    print("PRECISION AUDIT PASSED")
    for item in checks:
        print(f"  OK - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
