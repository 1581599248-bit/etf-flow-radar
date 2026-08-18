"""Fail-closed audit for the unified ETF Flow Radar data contract.

The audit is intentionally network-free.  It rejects a snapshot when numeric
facts, scope counts, terminology, classification status or provenance disagree.
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
    inc = sum(r.get("shareDirection1d") == "increase" for r in rows)
    dec = sum(r.get("shareDirection1d") == "decrease" for r in rows)
    flat = sum(r.get("shareDirection1d") == "unchanged" for r in rows)
    return inc, dec, flat


def audit(path: Path = SNAPSHOT) -> list[str]:
    snapshot = json.loads(path.read_text("utf-8"))
    checks: list[str] = []

    if snapshot.get("sourceMode") != "REAL":
        raise AssertionError("sourceMode must be REAL")
    if snapshot.get("status") not in {"verified", "warning"}:
        raise AssertionError(f"snapshot status not publishable: {snapshot.get('status')}")
    if snapshot.get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError("unified data contract not applied")
    if snapshot.get("quality", {}).get("dataContractVersion") != contract.CONTRACT_VERSION:
        raise AssertionError("quality data-contract marker mismatch")
    checks.append("real source, publish status and unified contract")

    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    if primary.get("metric") != "primaryMarketNetSubscriptionEstimate":
        raise AssertionError("canonical primary metric changed")
    if primary.get("valuation") != "sameDayUnitNAV":
        raise AssertionError("canonical valuation must be same-day unit NAV")
    if "一级市场" not in str(primary.get("displayName") or ""):
        raise AssertionError("primary display name must explicitly say primary market")
    if "T日单位净值" not in str(primary.get("definition") or ""):
        raise AssertionError("primary definition lost exact NAV valuation wording")
    checks.append("canonical primary-market definition")

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
    checks.append("unique codes, non-negative shares, positive NAV and canonical direction")

    scopes = primary.get("scopeTotals", {})
    market = snapshot.get("market", {})
    ashare = scopes.get("aShareStockEtf", {})
    _close(market.get("flow1d"), ashare.get("flow1d"), "market vs A-share primary flow")
    if int(market.get("etfCount") or 0) != int(ashare.get("etfCount") or -1):
        raise AssertionError("market ETF count differs from A-share primary scope")

    ashare_rows = [r for r in valid if r.get("assetScope") == "aShareStockEtf"]
    inc, dec, flat = _direction_counts(ashare_rows)
    expected = (inc, dec, flat)
    actual = (
        int(market.get("increaseEtfCount1d") or 0),
        int(market.get("decreaseEtfCount1d") or 0),
        int(market.get("unchangedEtfCount1d") or 0),
    )
    if actual != expected:
        raise AssertionError(f"market breadth differs from canonical directions: {actual} vs {expected}")
    if sum(actual) != int(market.get("etfCount") or -1):
        raise AssertionError("market direction counts do not sum to ETF count")
    checks.append("A-share market total and direction counts")

    assets = primary.get("assetClassTotals", {})
    if set(assets) != set(contract.ASSET_SCOPES):
        raise AssertionError(f"asset-class keys changed: {sorted(assets)}")
    asset_count = sum(int(v.get("etfCount") or 0) for v in assets.values())
    if asset_count != int(scopes.get("allEtf", {}).get("etfCount") or -1):
        raise AssertionError("mutually-exclusive asset-class counts do not reconcile")
    _close(sum(_num(v.get("flow1d"), f"asset {k}") for k, v in assets.items()), scopes.get("allEtf", {}).get("flow1d"), "asset flows vs all ETF")
    _close(primary.get("assetClassReconciliation", {}).get("difference"), 0.0, "stored asset reconciliation")
    if (
        int(assets["aShareStockEtf"].get("increaseEtfCount1d") or 0),
        int(assets["aShareStockEtf"].get("decreaseEtfCount1d") or 0),
        int(assets["aShareStockEtf"].get("unchangedEtfCount1d") or 0),
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
    for group in snapshot.get("groups", []):
        if group.get("classificationClaim") != "研究分组，不代表基金管理人或指数公司官方分类":
            raise AssertionError(f"group classification claim missing: {group.get('id')}")
    # Regression guard for the known generic-consumer false positive.
    row_510150 = next((r for r in universe if str(r.get("code")) == "510150"), None)
    if row_510150 and row_510150.get("groupId") == "sw_food_beverage" and row_510150.get("classificationStatus") != "ambiguous":
        raise AssertionError("510150 generic consumption ETF is still asserted as food-and-beverage")
    checks.append("conservative classification and ambiguity exclusion")

    # 5d/20d semantics: cumulative fields can be shown only if verified daily facts exist.
    for horizon in (5, 20):
        status = market.get(f"flow{horizon}dCumulativeStatus")
        cumulative = market.get(f"flow{horizon}dCumulative")
        display = market.get(f"flow{horizon}d")
        endpoint = market.get(f"flow{horizon}dEndpoint")
        if status == "available":
            _close(display, cumulative, f"{horizon}d display vs cumulative")
        else:
            if display is not None or cumulative is not None:
                raise AssertionError(f"{horizon}d cumulative displayed without full verified history")
        if endpoint is None:
            raise AssertionError(f"{horizon}d endpoint fact missing")
    for group in snapshot.get("groups", []):
        for horizon in (5, 20):
            if group.get(f"flow{horizon}dCumulativeStatus") != "available" and group.get(f"flow{horizon}d") is not None:
                raise AssertionError(f"group {group.get('id')} exposes incomplete {horizon}d cumulative flow")
            if group.get(f"flow{horizon}dEndpoint") is None:
                raise AssertionError(f"group {group.get('id')} lost {horizon}d endpoint fact")
    checks.append("true cumulative flow separated from endpoint share-change estimates")

    trade = snapshot.get("flowMetrics", {}).get("secondaryMarketTradeFlow", {})
    if trade:
        if trade.get("metric") != "secondaryMarketAggressorImbalanceEstimate":
            raise AssertionError("secondary trade metric still uses a cash-flow name")
        definition = str(trade.get("definition") or "")
        if "不代表市场净新增资金" not in definition or "不是ETF一级市场申购赎回" not in definition:
            raise AssertionError("secondary trade definition is not explicit enough")
    vendor = snapshot.get("flowMetrics", {}).get("secondaryMarketOrderFlow", {})
    if vendor and "行情商" not in str(vendor.get("displayName") or ""):
        raise AssertionError("vendor main-order field is not labelled as vendor-defined")
    checks.append("secondary trading statistics separated from primary subscriptions")

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

    # Plausibility guard: primary one-day market flow should not exceed half of
    # the entire A-share ETF NAV by absolute value without explicit manual review.
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
