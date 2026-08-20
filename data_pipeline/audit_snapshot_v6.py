"""Fail-closed external-send audit for an ETF Flow Radar schema-v6 snapshot.

This script is intentionally network-free. It verifies that every client-facing
number can be reconciled back to the same canonical snapshot before GitHub
Actions is allowed to commit or deploy it.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "site" / "data" / "latest.json"
TOL = 0.06

# Headline strength thresholds live in the pipeline; importing keeps this audit
# aligned with the client-facing copy instead of duplicating constants.
from update_daily_v2 import _trade_strength  # noqa: E402


def _num(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise AssertionError(f"{label} is not a finite number: {value!r}")
    return float(value)


def _close(left: Any, right: Any, label: str, tol: float = TOL) -> None:
    a, b = _num(left, f"{label}.left"), _num(right, f"{label}.right")
    if abs(a - b) > tol:
        raise AssertionError(f"{label} mismatch: {a} vs {b} (tol={tol})")


def audit(snapshot_path: Path) -> list[str]:
    snapshot = json.loads(snapshot_path.read_text("utf-8"))
    checks: list[str] = []

    if snapshot.get("schemaVersion") != 6:
        raise AssertionError("schemaVersion must be 6")
    if snapshot.get("sourceMode") != "REAL":
        raise AssertionError("sourceMode must be REAL")
    if snapshot.get("status") not in {"verified", "warning"}:
        raise AssertionError(f"snapshot status is not publishable: {snapshot.get('status')}")
    trade_date = str(snapshot.get("tradeDate") or "")
    if not trade_date:
        raise AssertionError("tradeDate is missing")
    checks.append("schema/source/status")

    quality = snapshot.get("quality", {})
    if int(quality.get("officialSessions") or 0) < 21:
        raise AssertionError("fewer than 21 official share sessions")
    coverage = _num(quality.get("classifiedCoverageOfMarketPct"), "classifiedCoverageOfMarketPct")
    if coverage < 95:
        raise AssertionError(f"classified market coverage below 95%: {coverage}%")
    checks.append("official history and classification coverage")

    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    if primary.get("metric") != "primaryMarketNetSubscriptionEstimate":
        raise AssertionError("canonical primary metric changed")
    if primary.get("valuation") != "sameDayUnitNAV":
        raise AssertionError("canonical primary valuation must be sameDayUnitNAV")
    scopes = primary.get("scopeTotals", {})
    market = snapshot.get("market", {})
    ashare = scopes.get("aShareStockEtf", {})
    _close(market.get("flow1d"), ashare.get("flow1d"), "market vs primary A-share flow")
    if int(market.get("etfCount") or 0) != int(ashare.get("etfCount") or -1):
        raise AssertionError("market ETF count differs from primary A-share scope")
    breadth_count = sum(int(market.get(key) or 0) for key in (
        "increaseEtfCount1d", "decreaseEtfCount1d", "unchangedEtfCount1d"
    ))
    if breadth_count != int(market.get("etfCount") or -1):
        raise AssertionError(f"A-share breadth counts do not sum to ETF count: {breadth_count}")
    checks.append("A-share market scope and breadth")

    assets = primary.get("assetClassTotals", {})
    expected_assets = {
        "aShareStockEtf", "crossBorderStockEtf", "bondEtf", "moneyEtf", "commodityEtf", "otherEtf"
    }
    if set(assets) != expected_assets:
        raise AssertionError(f"asset classes changed: {sorted(assets)}")
    asset_count = sum(int(row.get("etfCount") or 0) for row in assets.values())
    if asset_count != int(scopes.get("allEtf", {}).get("etfCount") or -1):
        raise AssertionError("mutually-exclusive asset-class counts do not reconcile to all ETFs")
    _close(
        sum(_num(row.get("flow1d"), f"asset {key} flow") for key, row in assets.items()),
        scopes.get("allEtf", {}).get("flow1d"),
        "asset classes vs all-ETF flow",
        tol=0.12,
    )
    recon = primary.get("assetClassReconciliation", {})
    _close(recon.get("difference"), 0.0, "stored asset-class reconciliation")
    _close(
        _num(assets["aShareStockEtf"].get("flow1d"), "A-share asset flow")
        + _num(assets["crossBorderStockEtf"].get("flow1d"), "cross-border asset flow"),
        scopes.get("stockEtfIncludingCrossBorder", {}).get("flow1d"),
        "stock incl cross-border flow",
        tol=0.12,
    )
    checks.append("mutually-exclusive asset-class reconciliation")

    etfs = snapshot.get("etfs", [])
    groups = snapshot.get("groups", [])
    non_a_share = [
        row for row in etfs if str(row.get("assetScope") or "") != "aShareStockEtf"
    ]
    if non_a_share:
        sample = [(row.get("code"), row.get("name"), row.get("assetScope")) for row in non_a_share[:5]]
        raise AssertionError(f"non-A-share ETFs leaked into client groups: {sample}")
    scope_guard = quality.get("classifiedAshareScopeEnforcement", {})
    if int(scope_guard.get("afterCount") or -1) != len(etfs):
        raise AssertionError("classified A-share scope guard count does not match snapshot.etfs")
    excluded_by_scope = scope_guard.get("excludedByScope", {}) or {}
    if "aShareStockEtf" in excluded_by_scope:
        raise AssertionError("A-share ETF was incorrectly excluded by the group-scope guard")
    checks.append("client groups contain domestic A-share stock ETFs only")

    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in etfs:
        gid = str(row.get("groupId") or "")
        if gid:
            by_group.setdefault(gid, []).append(row)
    for group in groups:
        gid = str(group.get("id") or "")
        members = by_group.get(gid, [])
        if not members:
            raise AssertionError(f"visible group has no ETF members: {gid}")
        member_flow = round(sum(_num(row.get("flow1d"), f"ETF {row.get('code')} flow1d") for row in members), 2)
        _close(group.get("flow1d"), member_flow, f"group {gid} vs member ETFs")
        if int(group.get("etfCount") or 0) != len(members):
            raise AssertionError(f"group {gid} ETF count mismatch")
        member_codes = {str(row.get("code", "")).zfill(6) for row in members}
        representative = str((group.get("representative") or {}).get("code") or "").zfill(6)
        if representative not in member_codes:
            raise AssertionError(
                f"group {gid} return representative is outside its A-share member set: {representative}"
            )
    classified_flow = round(sum(_num(g.get("flow1d"), f"group {g.get('id')} flow") for g in groups), 2)
    market_recon = quality.get("marketScopeReconciliation", {})
    stored_classified = market_recon.get("classifiedGroupShareFlow1d", market_recon.get("classifiedGroupPrimaryFlow1d"))
    _close(stored_classified, classified_flow, "classified group reconciliation")
    _close(
        _num(market.get("flow1d"), "market flow") - classified_flow,
        market_recon.get("ungroupedDifference"),
        "unclassified market difference",
    )
    checks.append("visible groups, representatives and member ETFs reconcile")

    visible_sectors = [g for g in groups if g.get("kind") == "industry"]
    if not visible_sectors:
        raise AssertionError("no visible SW/theme industry groups")
    group_by_id = {str(g.get("id")): g for g in visible_sectors}
    rollups = snapshot.get("industryRollups", [])
    for rollup in rollups:
        leaves = [group_by_id[str(gid)] for gid in rollup.get("leafGroups", []) if str(gid) in group_by_id]
        if not leaves:
            raise AssertionError(f"industry rollup has no visible leaf groups: {rollup.get('id')}")
        _close(
            rollup.get("flow1d"),
            round(sum(_num(g.get("flow1d"), f"leaf {g.get('id')} flow") for g in leaves), 2),
            f"industry rollup {rollup.get('id')}",
            tol=0.12,
        )
    visible_sector_flow = round(sum(_num(g.get("flow1d"), f"sector {g.get('id')} flow") for g in visible_sectors), 2)
    rollup_flow = round(sum(_num(g.get("flow1d"), f"rollup {g.get('id')} flow") for g in rollups), 2)
    _close(visible_sector_flow, rollup_flow, "visible sector layer vs SW rollups", tol=0.12)
    sector_recon = quality.get("clientSectorReconciliation", {})
    _close(sector_recon.get("visibleGroupFlow1d"), visible_sector_flow, "stored visible sector flow")
    _close(sector_recon.get("industryRollupFlow1d"), rollup_flow, "stored rollup sector flow")
    _close(sector_recon.get("difference"), 0.0, "stored sector-layer difference")
    checks.append("SW-level/theme display layer vs hidden SW rollups")

    headline = str(snapshot.get("conclusion", {}).get("headline") or "")
    primary_value = _num(market.get("flow1d"), "market flow for headline")
    # 措辞形容词（小幅/明显/大幅）随强度阈值调优可能变化，审计只对账数字与方向，
    # 措辞本身以 update_daily_v2 的生成为准。
    if primary_value == 0:
        primary_pattern = r"ETF份额对应申赎资金(?:基本持平|净额0\.0亿元)"
    else:
        primary_direction = "流入" if primary_value > 0 else "流出"
        primary_pattern = (
            rf"ETF份额对应申赎资金(?:小幅|明显|大幅)?净{primary_direction}{abs(primary_value):.1f}亿元"
        )
    if not re.search(primary_pattern, headline):
        raise AssertionError(f"headline primary phrase mismatch; expected /{primary_pattern}/")
    if "A股股票ETF当日合计" in headline:
        raise AssertionError("legacy primary headline wording leaked into client output")
    top_in = max(visible_sectors, key=lambda g: _num(g.get("flow1d"), "sector inflow rank"))
    top_out = min(visible_sectors, key=lambda g: _num(g.get("flow1d"), "sector outflow rank"))
    facts = snapshot.get("conclusion", {}).get("facts") or []
    sector_fact = str(facts[1]) if len(facts) > 1 else ""
    if top_in["name"] not in sector_fact or top_out["name"] not in sector_fact:
        raise AssertionError(
            f"facts[1] sector ranking is not from visible groups; expected {top_in['name']!r}/{top_out['name']!r} in {sector_fact!r}"
        )
    if f"{_num(top_in.get('flow1d'), 'top sector inflow'):+.1f}亿" not in sector_fact and _num(top_in.get("flow1d"), "top sector inflow") > 0:
        raise AssertionError("facts[1] top inflow amount mismatch")
    if f"{_num(top_out.get('flow1d'), 'top sector outflow'):+.1f}亿" not in sector_fact:
        raise AssertionError("facts[1] top outflow amount mismatch")
    if re.search(r"宽基\d+组中|申万一级和主题行业资金流入居前的是", headline):
        raise AssertionError("group/sector tail leaked back into headline; it belongs in facts")
    broad_fact = str(facts[0]) if facts else ""
    broad_groups = [g for g in snapshot.get("groups", []) if g.get("kind") == "broad"]
    broad_in = sum(_num(g.get("flow1d"), "broad flow") > 0 for g in broad_groups)
    broad_out = sum(_num(g.get("flow1d"), "broad flow") < 0 for g in broad_groups)
    if f"宽基{len(broad_groups)}组中{broad_out}个流出、{broad_in}个流入" not in broad_fact:
        raise AssertionError(f"facts[0] broad-group counts mismatch; got {broad_fact!r}")

    trade_metric = snapshot.get("flowMetrics", {}).get("secondaryMarketTradeFlow", {})
    if trade_metric.get("status") == "available":
        if trade_metric.get("tradeDate") != trade_date:
            raise AssertionError("secondary trading-flow date differs from snapshot trade date")
        trade_scope = trade_metric.get("scopeTotals", {}).get("aShareStockEtf", {})
        trade_value = _num(trade_scope.get("netFlow1d"), "A-share secondary trade flow")
        raw_inflow = trade_scope.get("inflow1d")
        raw_outflow = trade_scope.get("outflow1d")
        trade_turnover = None
        if (
            isinstance(raw_inflow, (int, float))
            and isinstance(raw_outflow, (int, float))
            and math.isfinite(float(raw_inflow))
            and math.isfinite(float(raw_outflow))
        ):
            trade_turnover = float(raw_inflow) + float(raw_outflow)
        if _trade_strength(trade_value, trade_turnover) == "balanced":
            trade_pattern = r"^A股ETF盘中买卖力量基本均衡；"
        else:
            trade_direction = "买入" if trade_value > 0 else "卖出"
            trade_pattern = (
                rf"^A股ETF盘中(?:买|卖)盘(?:小幅偏强|偏强|明显占优)，"
                rf"主动{trade_direction}净额{abs(trade_value):.1f}亿元；"
            )
        if not re.search(trade_pattern, headline):
            raise AssertionError(f"headline secondary flow mismatch; expected /{trade_pattern}/")
    elif not headline.startswith("A股ETF盘中主动买卖数据暂缺；"):
        raise AssertionError("unavailable secondary flow is not labelled as unavailable")
    checks.append("client headline uses the same displayed data layer")

    if market.get("multiDayMethod") != "endpoint_share_change_times_current_nav":
        raise AssertionError("5d/20d market fields are no longer explicitly endpoint metrics")
    for group in groups:
        if group.get("flow5dMetric") != "endpointShareChangeTimesCurrentNAV" or group.get("flow20dMetric") != "endpointShareChangeTimesCurrentNAV":
            raise AssertionError(f"group {group.get('id')} lost endpoint metric labels")
    if "不是逐日净" not in str(snapshot.get("methodology", {}).get("multiDay") or ""):
        raise AssertionError("methodology no longer warns that 5d/20d are endpoint metrics")
    checks.append("5d/20d endpoint semantics")

    daily_path = snapshot_path.parent / "daily" / f"{trade_date}.json"
    if not daily_path.exists():
        raise AssertionError(f"daily audit payload missing: {daily_path}")
    daily = json.loads(daily_path.read_text("utf-8"))
    if daily.get("tradeDate") != trade_date:
        raise AssertionError("daily payload trade date mismatch")
    if daily.get("metric") != primary.get("metric") or daily.get("valuation") != primary.get("valuation"):
        raise AssertionError("daily payload metric/valuation mismatch")
    daily_ashare = daily.get("marketScopes", {}).get("aShareStockEtf", {})
    _close(daily_ashare.get("flow1d"), market.get("flow1d"), "daily payload vs headline market flow")
    checks.append("persisted daily fact vs latest snapshot")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", nargs="?", default=str(DEFAULT_SNAPSHOT))
    args = parser.parse_args()
    path = Path(args.snapshot)
    try:
        checks = audit(path)
    except Exception as exc:
        print(f"EXTERNAL-SEND AUDIT FAILED: {exc}")
        return 1
    print(f"EXTERNAL-SEND AUDIT PASSED: {path}")
    for check in checks:
        print(f"  OK - {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())