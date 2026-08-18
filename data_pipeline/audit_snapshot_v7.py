"""Fail-closed audit for the unified ETF Flow Radar Data Contract 7.0.

The audit is network-free. It rejects a snapshot whenever economic definitions,
source provenance, counts, classifications, cumulative history, representatives,
JSON field names or client wording disagree.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import system_contract_v7 as contract

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "site" / "data" / "latest.json"
PAGE = ROOT / "site" / "index.html"
BUILD = ROOT / "scripts" / "build-site.mjs"
TOL = 0.12


def _num(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise AssertionError(f"{label} is not finite: {value!r}")
    return float(value)


def _close(a: Any, b: Any, label: str, tol: float = TOL) -> None:
    left, right = _num(a, f"{label}.left"), _num(b, f"{label}.right")
    if abs(left - right) > tol:
        raise AssertionError(f"{label} mismatch: {left} vs {right}")


def _direction_counts(rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    return (
        sum(r.get("shareDirection1d") == "increase" for r in rows),
        sum(r.get("shareDirection1d") == "decrease" for r in rows),
        sum(r.get("shareDirection1d") == "unchanged" for r in rows),
    )


def audit(path: Path = SNAPSHOT) -> list[str]:
    snapshot = json.loads(path.read_text("utf-8"))
    checks: list[str] = []
    quality = snapshot.get("quality", {})

    if snapshot.get("sourceMode") != "REAL":
        raise AssertionError("sourceMode must be REAL")
    if snapshot.get("status") not in {"verified", "warning"}:
        raise AssertionError(f"snapshot status not publishable: {snapshot.get('status')}")
    if snapshot.get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError("unified data contract not applied")
    if quality.get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError("quality data-contract marker mismatch")
    digest = str(snapshot.get("classificationRuleDigest") or "")
    if len(digest) != 64 or quality.get("classificationRuleDigest") != digest:
        raise AssertionError("classification-rule digest missing or inconsistent")
    checks.append("real source, publish status, data contract and classification digest")

    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    if primary.get("metric") != "primaryMarketNetSubscriptionEstimate":
        raise AssertionError("canonical primary metric changed")
    if primary.get("valuation") != "sameDayUnitNAV":
        raise AssertionError("canonical valuation must be same-day unit NAV")
    if "一级市场" not in str(primary.get("displayName") or ""):
        raise AssertionError("primary display name must explicitly say primary market")
    if "T日单位净值" not in str(primary.get("definition") or ""):
        raise AssertionError("primary definition lost exact NAV valuation wording")
    multi = primary.get("multiDay", {})
    if multi.get("cumulative", {}).get("method") != "sumOfSameContractVerifiedDailyPrimaryFlows":
        raise AssertionError("primary cumulative method is not same-contract daily summation")
    if multi.get("endpoint", {}).get("method") != "endpointShareChangeTimesCurrentNAV":
        raise AssertionError("primary endpoint method changed")
    checks.append("canonical primary-market and multi-day definitions")

    universe = snapshot.get("universe", [])
    if not universe:
        raise AssertionError("universe is empty")
    codes = [str(r.get("code", "")).zfill(6) for r in universe]
    if len(codes) != len(set(codes)):
        raise AssertionError("duplicate ETF codes in complete universe")
    valid = [r for r in universe if isinstance(r.get("primaryFlow1d"), (int, float))]
    for row in valid:
        if row.get("shareDirection1d") not in {"increase", "decrease", "unchanged"}:
            raise AssertionError(f"missing canonical direction for {row.get('code')}")
        if not isinstance(row.get("shares"), (int, float)) or float(row["shares"]) < 0:
            raise AssertionError(f"invalid shares for {row.get('code')}")
        if not isinstance(row.get("nav"), (int, float)) or float(row["nav"]) <= 0:
            raise AssertionError(f"invalid NAV for {row.get('code')}")
        if row.get("classificationStatus") == "ambiguous" and any(k in row for k in ("groupId", "groupName", "kind")):
            raise AssertionError(f"ambiguous universe row still asserts a classification: {row.get('code')}")
    checks.append("unique codes, valid primary facts and unambiguous universe semantics")

    scopes = primary.get("scopeTotals", {})
    market = snapshot.get("market", {})
    ashare = scopes.get("aShareStockEtf", {})
    _close(market.get("flow1d"), ashare.get("flow1d"), "market vs A-share primary flow")
    if int(market.get("etfCount") or 0) != int(ashare.get("etfCount") or -1):
        raise AssertionError("market ETF count differs from A-share primary scope")

    ashare_rows = [r for r in valid if r.get("assetScope") == "aShareStockEtf"]
    expected = _direction_counts(ashare_rows)
    actual = (
        int(market.get("increaseEtfCount1d") or 0),
        int(market.get("decreaseEtfCount1d") or 0),
        int(market.get("unchangedEtfCount1d") or 0),
    )
    if actual != expected:
        raise AssertionError(f"market breadth differs from canonical directions: {actual} vs {expected}")
    if sum(actual) != int(market.get("etfCount") or -1):
        raise AssertionError("market direction counts do not sum to ETF count")
    checks.append("A-share market total and canonical direction counts")

    assets = primary.get("assetClassTotals", {})
    if set(assets) != set(contract.ASSET_SCOPES):
        raise AssertionError(f"asset-class keys changed: {sorted(assets)}")
    asset_count = sum(int(v.get("etfCount") or 0) for v in assets.values())
    if asset_count != int(scopes.get("allEtf", {}).get("etfCount") or -1):
        raise AssertionError("mutually-exclusive asset-class counts do not reconcile")
    _close(
        sum(_num(v.get("flow1d"), f"asset {k}") for k, v in assets.items()),
        scopes.get("allEtf", {}).get("flow1d"),
        "asset flows vs all ETF",
    )
    _close(primary.get("assetClassReconciliation", {}).get("difference"), 0.0, "stored asset reconciliation")
    a_asset = assets["aShareStockEtf"]
    if (
        int(a_asset.get("increaseEtfCount1d") or 0),
        int(a_asset.get("decreaseEtfCount1d") or 0),
        int(a_asset.get("unchangedEtfCount1d") or 0),
    ) != actual:
        raise AssertionError("asset-class breadth differs from market breadth")
    checks.append("asset-class flow and breadth reconciliation")

    client_etfs = snapshot.get("etfs", [])
    ambiguous_codes = {
        str(r.get("code", "")).zfill(6)
        for r in universe if r.get("classificationStatus") == "ambiguous"
    }
    leaked = [r for r in client_etfs if str(r.get("code", "")).zfill(6) in ambiguous_codes]
    if leaked:
        raise AssertionError(f"ambiguous classification leaked into client groups: {leaked[:3]}")
    for row in client_etfs:
        if row.get("assetScope") != "aShareStockEtf":
            raise AssertionError(f"non-A-share ETF leaked into client groups: {row.get('code')}")
        if row.get("classificationMethod") != "fund_name_rule":
            raise AssertionError(f"classification method missing for {row.get('code')}")
        if "flow5d" in row or "flow20d" in row:
            raise AssertionError(f"ETF {row.get('code')} still exposes ambiguous v6 flow5d/flow20d fields")

    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in client_etfs:
        by_group.setdefault(str(row.get("groupId") or ""), []).append(row)
    for group in snapshot.get("groups", []):
        gid = str(group.get("id") or "")
        members = by_group.get(gid, [])
        if not members:
            raise AssertionError(f"visible group has no ETF members: {gid}")
        if group.get("classificationClaim") != "研究分组，不代表基金管理人或指数公司官方分类":
            raise AssertionError(f"group classification claim missing: {gid}")
        member_flow = round(sum(_num(r.get("flow1d"), f"ETF {r.get('code')} flow1d") for r in members), 2)
        _close(group.get("flow1d"), member_flow, f"group {gid} vs members")
        if int(group.get("etfCount") or 0) != len(members):
            raise AssertionError(f"group ETF count mismatch: {gid}")
        member_codes = {str(r.get("code", "")).zfill(6) for r in members}
        rep_code = str((group.get("representative") or {}).get("code") or "").zfill(6)
        if rep_code not in member_codes:
            raise AssertionError(f"group representative is outside current members: {gid} -> {rep_code}")
        if group.get("priceFlowStateMetric") != "representativeEtfRelativeReturn20d_vs_endpointShareChangeIntensity5d":
            raise AssertionError(f"group price/share-state metric mismatch: {gid}")
        for horizon in (5, 20):
            if group.get(f"flow{horizon}dEndpoint") is None:
                raise AssertionError(f"group {gid} lost {horizon}d endpoint fact")
            explicit = (
                int(group.get(f"increaseEtfCount{horizon}dEndpoint") or 0),
                int(group.get(f"decreaseEtfCount{horizon}dEndpoint") or 0),
                int(group.get(f"unchangedEtfCount{horizon}dEndpoint") or 0),
            )
            legacy_alias = (
                int(group.get(f"increaseEtfCount{horizon}d") or 0),
                int(group.get(f"decreaseEtfCount{horizon}d") or 0),
                int(group.get(f"unchangedEtfCount{horizon}d") or 0),
            )
            if explicit != legacy_alias:
                raise AssertionError(f"group {gid} endpoint breadth aliases diverge for {horizon}d")
            if sum(explicit) != int(group.get(f"endpointBreadthSampleCount{horizon}d") or 0):
                raise AssertionError(f"group {gid} endpoint breadth sample mismatch for {horizon}d")
    checks.append("conservative classification, group reconciliation and representative validity")

    row_510150 = next((r for r in universe if str(r.get("code")) == "510150"), None)
    if row_510150 and row_510150.get("candidateGroupId") == "sw_food_beverage":
        if row_510150.get("classificationStatus") != "ambiguous":
            raise AssertionError("510150 generic consumption ETF is still asserted as food-and-beverage")
    checks.append("known classification false-positive regression guard")

    if market.get("multiDayMethod") != "sum_of_same_contract_verified_daily_primary_flows":
        raise AssertionError("market multi-day method is not true same-contract daily summation")
    if market.get("endpointMethod") != "endpoint_share_change_times_current_nav":
        raise AssertionError("market endpoint method changed")
    for horizon in (5, 20):
        status = market.get(f"flow{horizon}dCumulativeStatus")
        cumulative = market.get(f"flow{horizon}dCumulative")
        display = market.get(f"flow{horizon}d")
        endpoint = market.get(f"flow{horizon}dEndpoint")
        dates = market.get(f"flow{horizon}dCumulativeSourceDates") or []
        if status == "available":
            _close(display, cumulative, f"{horizon}d display vs cumulative")
            if len(dates) != horizon:
                raise AssertionError(f"{horizon}d cumulative source-date count mismatch")
        else:
            if display is not None or cumulative is not None or dates:
                raise AssertionError(f"{horizon}d cumulative exposed without complete same-contract history")
        if endpoint is None:
            raise AssertionError(f"{horizon}d endpoint market fact missing")

    for group in snapshot.get("groups", []):
        for horizon in (5, 20):
            status = group.get(f"flow{horizon}dCumulativeStatus")
            value = group.get(f"flow{horizon}d")
            dates = group.get(f"flow{horizon}dCumulativeSourceDates") or []
            if status == "available":
                if value is None or len(dates) != horizon:
                    raise AssertionError(f"group {group.get('id')} incomplete available {horizon}d cumulative")
            elif value is not None or dates:
                raise AssertionError(f"group {group.get('id')} exposes incomplete {horizon}d cumulative")
    checks.append("same-contract cumulative flow separated from endpoint share-change estimates")

    metrics = snapshot.get("flowMetrics", {})
    if "secondaryMarketTradeFlow" in metrics or "secondaryMarketOrderFlow" in metrics:
        raise AssertionError("legacy secondary-market metric keys remain in v7 snapshot")
    trade = metrics.get("secondaryMarketAggressorImbalance", {})
    if trade:
        if trade.get("metric") != "secondaryMarketAggressorImbalanceEstimate":
            raise AssertionError("secondary aggressor metric name mismatch")
        definition = str(trade.get("definition") or "")
        if "不代表市场净新增资金" not in definition or "不是ETF一级市场申购赎回" not in definition:
            raise AssertionError("secondary aggressor definition is not explicit enough")
        for scope in trade.get("scopeTotals", {}).values():
            if any(k in scope for k in ("netFlow1d", "inflow1d", "outflow1d")):
                raise AssertionError("legacy cash-flow names remain in aggressor scope")
    vendor = metrics.get("secondaryMarketVendorMainOrder", {})
    if vendor:
        if "行情商" not in str(vendor.get("displayName") or ""):
            raise AssertionError("vendor main-order metric is not labelled as vendor-defined")
        for scope in vendor.get("scopeTotals", {}).values():
            if "flow1d" in scope:
                raise AssertionError("vendor-defined secondary field still uses generic flow1d")
    for row in client_etfs:
        if "secondaryTradeNetFlow1d" in row or "secondaryMainOrderFlow1d" in row:
            raise AssertionError(f"legacy secondary ETF field remains: {row.get('code')}")
    checks.append("secondary trading statistics use non-cash-flow JSON names")

    if "industryRollups" in snapshot:
        raise AssertionError("legacy industryRollups key remains")
    for rollup in snapshot.get("industryResearchRollups", []):
        if "研究汇总" not in str(rollup.get("classificationClaim") or ""):
            raise AssertionError(f"research rollup lacks classification boundary: {rollup.get('id')}")
    checks.append("industry/theme aggregates are explicitly research rollups")

    conclusion = snapshot.get("conclusion", {})
    headline = str(conclusion.get("headline") or "")
    if "估算" not in headline or "交易所日终份额变化" not in headline:
        raise AssertionError("headline does not state the primary estimate and source fact")
    if "当日成交资金净" in headline or "申万一级和主题行业" in headline:
        raise AssertionError("legacy ambiguous headline wording leaked into client conclusion")
    interpretation = str(conclusion.get("interpretation") or "")
    if "不等同于二级市场成交资金" not in interpretation:
        raise AssertionError("conclusion interpretation lacks economic-variable separation")
    checks.append("client conclusion wording")

    methodology = snapshot.get("methodology", {})
    required_phrases = {
        "flow": "清算后份额",
        "direction": "shareDirection1d",
        "multiDay": "逐日累计净申赎",
        "classification": "ambiguous",
        "coordinates": "代表ETF",
        "secondary": "不是ETF一级市场净申购/赎回",
        "scope": "A股股票ETF",
        "valuation": "同日单位净值",
    }
    for key, phrase in required_phrases.items():
        if phrase not in str(methodology.get(key) or ""):
            raise AssertionError(f"methodology.{key} missing phrase: {phrase}")
    provenance = snapshot.get("provenance", {})
    for key in ("primaryShares", "navAndFundType", "averagePriceComparison", "secondaryTrading"):
        if key not in provenance:
            raise AssertionError(f"missing provenance block: {key}")
    checks.append("methodology and provenance")

    page = PAGE.read_text("utf-8")
    build = BUILD.read_text("utf-8")
    forbidden_source_phrases = (
        "5日累计资金变化",
        "20日累计资金变化",
        "当日成交资金净流入/净流出",
        "申万行业口径",
        "申万行业分类标准2021版",
        "资金为组内ETF近5日净流入/流出",
    )
    for phrase in forbidden_source_phrases:
        if phrase in page:
            raise AssertionError(f"legacy client phrase remains in site/index.html: {phrase}")
    if "textReplacements" in build or "replaceAll(from, to)" in build:
        raise AssertionError("build step still rewrites methodology wording")
    checks.append("source page and build use one wording contract")

    aum = market.get("aum")
    if _num(aum, "market AUM") > 0 and abs(_num(market.get("flow1d"), "market flow")) > 0.5 * float(aum):
        raise AssertionError("A-share one-day primary flow exceeds 50% of market AUM")
    checks.append("basic numeric plausibility")

    return checks


def main() -> int:
    try:
        checks = audit()
    except Exception as exc:
        print(f"UNIFIED CONTRACT AUDIT FAILED: {exc}")
        return 1
    print("UNIFIED CONTRACT AUDIT PASSED")
    for item in checks:
        print(f"  OK - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
