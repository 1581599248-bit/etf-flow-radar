"""Fail-closed audit for exact-yuan multi-day cumulative ETF primary flows."""
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


def _close(actual: Any, expected: float, label: str, tol: float = YUAN_TOL) -> None:
    if not _finite(actual):
        raise AssertionError(f"{label} not finite: {actual!r}")
    if abs(float(actual) - expected) > tol:
        raise AssertionError(f"{label} mismatch: {actual} vs {expected:.2f}")


def _load_daily(parent: Path, stamp: str) -> dict[str, Any]:
    path = parent / "daily" / f"{stamp}.json"
    if not path.exists():
        raise AssertionError(f"cumulative source daily file missing: {path}")
    payload = json.loads(path.read_text("utf-8"))
    if payload.get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError(f"daily {stamp} uses a different data contract")
    if payload.get("aggregationMethod1d") != "sum_unrounded_share_delta_times_same_day_nav_then_round":
        raise AssertionError(f"daily {stamp} lacks exact-yuan aggregation contract")
    return payload


def _etf_map(payload: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("code", "")).zfill(6): float(row["primaryFlow1dYuanEstimate"])
        for row in payload.get("etfs", [])
        if _finite(row.get("primaryFlow1dYuanEstimate"))
    }


def _group_map(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in payload.get("etfs", []):
        gid = str(row.get("groupId") or "")
        value = row.get("primaryFlow1dYuanEstimate")
        if gid and _finite(value):
            result[gid] = result.get(gid, 0.0) + float(value)
    return result


def audit(path: Path = SNAPSHOT) -> list[str]:
    snapshot = json.loads(path.read_text("utf-8"))
    if snapshot.get("schemaVersion") != 7 or snapshot.get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError("cumulative audit requires client schema 7 / Data Contract 7.0")
    checks: list[str] = []
    parent = path.parent
    current_date = str(snapshot.get("tradeDate") or "")
    digest = str(snapshot.get("classificationRuleDigest") or "")
    current_market_yuan = snapshot.get("market", {}).get("primaryFlow1dYuanEstimate")
    current_etf = {
        str(row.get("code", "")).zfill(6): float(row["primaryFlow1dYuanEstimate"])
        for row in snapshot.get("etfs", []) if _finite(row.get("primaryFlow1dYuanEstimate"))
    }
    current_group = {
        str(group.get("id") or ""): float(group["primaryFlow1dYuanEstimate"])
        for group in snapshot.get("groups", []) if _finite(group.get("primaryFlow1dYuanEstimate"))
    }

    for horizon in (5, 20):
        market = snapshot.get("market", {})
        status = market.get(f"flow{horizon}dCumulativeStatus")
        dates = list(market.get(f"flow{horizon}dCumulativeSourceDates") or [])
        stored_yuan = market.get(f"flow{horizon}dCumulativeYuanEstimate")
        if status != "available":
            if dates or stored_yuan is not None or market.get(f"flow{horizon}d") is not None:
                raise AssertionError(f"{horizon}d market cumulative exposes data while status is unavailable")
            continue
        if len(dates) != horizon or dates[-1] != current_date:
            raise AssertionError(f"{horizon}d source-date list invalid: {dates}")

        market_sum = 0.0
        etf_accum = {code: 0.0 for code in current_etf}
        group_accum = {gid: 0.0 for gid in current_group}
        classified_comparable = True
        for stamp in dates:
            if stamp == current_date:
                if not _finite(current_market_yuan):
                    raise AssertionError("current market exact-yuan fact missing")
                market_sum += float(current_market_yuan)
                etf_map, group_map, day_digest = current_etf, current_group, digest
            else:
                payload = _load_daily(parent, stamp)
                value = payload.get("marketScopes", {}).get("aShareStockEtf", {}).get("primaryFlow1dYuanEstimate")
                if not _finite(value):
                    raise AssertionError(f"daily {stamp} market exact-yuan fact missing")
                market_sum += float(value)
                etf_map, group_map = _etf_map(payload), _group_map(payload)
                day_digest = str(payload.get("classificationRuleDigest") or "")
            if day_digest != digest:
                classified_comparable = False
            if classified_comparable:
                for code in etf_accum:
                    etf_accum[code] += float(etf_map.get(code, 0.0))
                for gid in group_accum:
                    group_accum[gid] += float(group_map.get(gid, 0.0))

        _close(stored_yuan, market_sum, f"market {horizon}d cumulative yuan")
        expected_yi = round(market_sum / 1e8, 2)
        if market.get(f"flow{horizon}d") != expected_yi:
            raise AssertionError(f"market {horizon}d display amount is not final-round exact-yuan sum")

        for group in snapshot.get("groups", []):
            group_status = group.get(f"flow{horizon}dCumulativeStatus")
            if classified_comparable:
                if group_status != "available":
                    raise AssertionError(f"group {group.get('id')} withheld despite comparable exact-yuan history")
                expected = group_accum.get(str(group.get("id") or ""), 0.0)
                _close(group.get(f"flow{horizon}dCumulativeYuanEstimate"), expected, f"group {group.get('id')} {horizon}d yuan")
                if group.get(f"flow{horizon}d") != round(expected / 1e8, 2):
                    raise AssertionError(f"group {group.get('id')} {horizon}d display rounding mismatch")
            elif group_status == "available":
                raise AssertionError(f"group {group.get('id')} mixes different classification digests")

        for row in snapshot.get("etfs", []):
            row_status = row.get(f"flow{horizon}dCumulativeStatus")
            if classified_comparable:
                if row_status != "available":
                    raise AssertionError(f"ETF {row.get('code')} withheld despite comparable exact-yuan history")
                expected = etf_accum.get(str(row.get("code", "")).zfill(6), 0.0)
                _close(row.get(f"flow{horizon}dCumulativeYuanEstimate"), expected, f"ETF {row.get('code')} {horizon}d yuan")
            elif row_status == "available":
                raise AssertionError(f"ETF {row.get('code')} mixes different classification digests")
        checks.append(f"{horizon}d exact-yuan cumulative reconstruction")

    multi = snapshot.get("flowMetrics", {}).get("primaryMarket", {}).get("multiDay", {}).get("cumulative", {})
    if multi.get("method") != "sumOfSameContractVerifiedDailyPrimaryFlows":
        raise AssertionError("multi-day semantic method changed")
    if multi.get("aggregation") != "sumUnroundedDailyPrimaryFlowYuanThenRound":
        raise AssertionError("multi-day exact-yuan aggregation marker missing")
    checks.append("multi-day method and aggregation contract")
    return checks


def main() -> int:
    try:
        checks = audit()
    except Exception as exc:
        print(f"CUMULATIVE PRECISION AUDIT FAILED: {exc}")
        return 1
    print("CUMULATIVE PRECISION AUDIT PASSED")
    for check in checks:
        print(f"  OK - {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
