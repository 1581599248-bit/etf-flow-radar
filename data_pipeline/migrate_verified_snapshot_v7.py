"""Offline bootstrap migration for the last already-verified ETF snapshot.

Purpose
-------
Data Contract 7.0 intentionally blocks legacy client snapshots. If a fresh
exchange/NAV production run is temporarily unavailable immediately after a
contract deployment, this script upgrades only the *schema and semantics* of
the last verified snapshot so the client can continue to display its original
trade date and original canonical one-day facts.

Hard safety boundary
--------------------
- No network access.
- No new ETF shares, NAVs, prices or primary one-day flows are invented.
- The original tradeDate and generatedAt are preserved.
- The verified v6 aggregate primary-flow totals are preserved. v6 stored those
  totals from unrounded calculations while per-ETF JSON rows were rounded to
  0.01亿元; migration must not replace the authoritative aggregate with a sum
  of rounded display rows.
- True 5d/20d cumulative flow is not backfilled from legacy endpoint fields.
- The migrated output must pass audit_snapshot_v7.py before workflows commit it.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import contract_finalizer_v7 as finalizer
import system_contract_v7 as contract

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "site" / "data" / "latest.json"
CN = ZoneInfo("Asia/Shanghai")
CLIENT_SNAPSHOT_SCHEMA_VERSION = 7


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _rounding_bound(count: Any) -> float:
    """Maximum expected drift from summing values rounded to 0.01亿元.

    schema-v6 persisted each ETF primaryFlow1d at two decimals but calculated
    aggregate scope totals before that row-level rounding. Summing N persisted
    rows can therefore differ from the verified aggregate by at most roughly
    N*0.005亿元. A small fixed allowance covers the final aggregate rounding.
    """
    try:
        n = max(int(count or 0), 0)
    except (TypeError, ValueError):
        n = 0
    return n * 0.005 + 0.05


def _preserve_verified_aggregate_precision(snapshot: dict, original: dict) -> None:
    """Restore verified v6 aggregate flows after row-level v7 normalization.

    The v7 canonicalizer must rebuild directions/counts from individual ETF
    facts, but a legacy snapshot cannot reconstruct the pre-rounding per-ETF
    values that produced the already-verified v6 aggregate. Replacing the old
    aggregate with a sum of two-decimal JSON rows would create a migration-only
    change in the market fact. We therefore preserve only the old aggregate
    flow1d values, after verifying that any difference is bounded by the known
    row-rounding envelope. Counts, directions, classifications and all other
    semantics continue to come from the v7 canonicalizer.
    """
    old_primary = original.get("flowMetrics", {}).get("primaryMarket", {})
    new_primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    old_scopes = old_primary.get("scopeTotals", {})
    new_scopes = new_primary.setdefault("scopeTotals", {})
    old_assets = old_primary.get("assetClassTotals", {})
    new_assets = new_primary.setdefault("assetClassTotals", {})

    scope_residuals: dict[str, float] = {}
    asset_residuals: dict[str, float] = {}

    def preserve(label: str, old_row: dict, new_row: dict, residuals: dict[str, float]) -> None:
        old_value = old_row.get("flow1d")
        new_value = new_row.get("flow1d")
        if not (_finite(old_value) and _finite(new_value)):
            return
        old_float = round(float(old_value), 2)
        new_float = round(float(new_value), 2)
        residual = round(old_float - new_float, 2)
        bound = _rounding_bound(old_row.get("etfCount", new_row.get("etfCount")))
        if abs(residual) > bound + 1e-9:
            raise AssertionError(
                f"bootstrap {label} aggregate drift {residual} exceeds legacy row-rounding bound {bound:.2f}"
            )
        new_row["flow1d"] = old_float
        residuals[label] = residual

    for scope in ("allEtf", "stockEtfIncludingCrossBorder", "aShareStockEtf"):
        if isinstance(old_scopes.get(scope), dict) and isinstance(new_scopes.get(scope), dict):
            preserve(scope, old_scopes[scope], new_scopes[scope], scope_residuals)

    for scope in contract.ASSET_SCOPES:
        if isinstance(old_assets.get(scope), dict) and isinstance(new_assets.get(scope), dict):
            preserve(scope, old_assets[scope], new_assets[scope], asset_residuals)

    ashare = new_scopes.get("aShareStockEtf", {})
    if _finite(ashare.get("flow1d")):
        snapshot.setdefault("market", {})["flow1d"] = round(float(ashare["flow1d"]), 2)

    if new_assets:
        values = [new_assets.get(scope, {}).get("flow1d") for scope in contract.ASSET_SCOPES]
        if all(_finite(value) for value in values):
            asset_sum = round(sum(float(value) for value in values), 2)
            all_total = new_scopes.get("allEtf", {}).get("flow1d")
            difference = round(asset_sum - float(all_total), 2) if _finite(all_total) else None
            new_primary["assetClassReconciliation"] = {
                "sumOfMutuallyExclusiveAssetClasses": asset_sum,
                "allEtfScopeTotal": round(float(all_total), 2) if _finite(all_total) else None,
                "difference": difference,
            }

    snapshot.setdefault("quality", {})["bootstrapAggregatePrecision"] = {
        "source": "previously_verified_schema_v6_aggregate_primary_flows",
        "reason": "schema_v6_per_etf_json_primary_flows_were_rounded_to_0.01_yi_after_aggregate_calculation",
        "scopeFlowRoundingResiduals": scope_residuals,
        "assetClassFlowRoundingResiduals": asset_residuals,
        "newMarketFactsCollected": False,
    }


def migrate(snapshot: dict) -> dict:
    if snapshot.get("dataContractVersion") == contract.CONTRACT_VERSION and snapshot.get("schemaVersion") == CLIENT_SNAPSHOT_SCHEMA_VERSION:
        return snapshot
    if snapshot.get("sourceMode") != "REAL":
        raise ValueError("legacy snapshot is not REAL-source data")
    if snapshot.get("status") not in {"verified", "warning"}:
        raise ValueError(f"legacy snapshot is not publishable: {snapshot.get('status')}")
    if not snapshot.get("tradeDate"):
        raise ValueError("legacy snapshot has no tradeDate")
    if not snapshot.get("universe") or not snapshot.get("flowMetrics"):
        raise ValueError("legacy snapshot lacks canonical universe/flow facts")

    original = copy.deepcopy(snapshot)
    original_trade_date = snapshot.get("tradeDate")
    original_generated_at = snapshot.get("generatedAt")
    original_market_flow = snapshot.get("market", {}).get("flow1d")
    original_market_count = snapshot.get("market", {}).get("etfCount")
    original_primary = (
        snapshot.get("flowMetrics", {})
        .get("primaryMarket", {})
        .get("scopeTotals", {})
        .get("aShareStockEtf", {})
        .get("flow1d")
    )
    if _finite(original_market_flow) and _finite(original_primary):
        if round(float(original_market_flow), 2) != round(float(original_primary), 2):
            raise AssertionError(
                f"legacy verified snapshot market/primary mismatch: {original_market_flow} vs {original_primary}"
            )

    # Apply only operations that can be derived from facts already persisted in
    # the verified snapshot. No exchange/NAV refetch is needed or allowed.
    contract.canonicalize_directions_and_totals(snapshot)
    contract.sanitize_classification(snapshot)
    contract.harmonize_secondary_metrics(snapshot)

    # Critical migration boundary: canonicalization above sums persisted
    # per-ETF values that schema v6 rounded to 0.01亿元. Restore the already
    # verified pre-row-rounding aggregates before client reconciliation and
    # conclusion text are rebuilt, otherwise a contract-only deployment can
    # change the market total and fail closed forever.
    _preserve_verified_aggregate_precision(snapshot, original)

    contract.rebuild_client_reconciliation(snapshot)
    contract.rebuild_conclusion(snapshot)
    contract.apply_wording_and_provenance(snapshot)

    # A bootstrap migration deliberately has no same-contract daily history.
    # The finalizer therefore publishes endpoint fields separately and leaves
    # true 5d/20d cumulative values unavailable until Contract 7.0 facts accrue.
    snapshot.setdefault("quality", {})["cumulativeFlowHistory"] = {
        "officialSessionDates": [str(original_trade_date)],
        "bootstrapMigration": True,
    }
    finalizer.finalize(snapshot)
    snapshot["schemaVersion"] = CLIENT_SNAPSHOT_SCHEMA_VERSION
    snapshot["quality"]["clientSnapshotSchemaVersion"] = CLIENT_SNAPSHOT_SCHEMA_VERSION

    # Never make a migrated snapshot look like newly collected market data.
    snapshot["tradeDate"] = original_trade_date
    snapshot["generatedAt"] = original_generated_at
    snapshot["contractMigratedAt"] = datetime.now(CN).isoformat(timespec="seconds")
    snapshot["quality"]["contractMigration"] = {
        "fromSchemaVersion": original.get("schemaVersion"),
        "fromDataContractVersion": original.get("dataContractVersion"),
        "toSchemaVersion": CLIENT_SNAPSHOT_SCHEMA_VERSION,
        "toDataContractVersion": contract.CONTRACT_VERSION,
        "mode": "offline_semantic_migration_of_previously_verified_snapshot",
        "preservedTradeDate": original_trade_date,
        "preservedGeneratedAt": original_generated_at,
        "networkAccess": False,
        "newMarketFactsCollected": False,
        "trueMultiDayCumulativeBackfilled": False,
        "preservedVerifiedAggregatePrecision": True,
    }

    migrated_flow = snapshot.get("market", {}).get("flow1d")
    migrated_count = snapshot.get("market", {}).get("etfCount")
    migrated_primary = (
        snapshot.get("flowMetrics", {})
        .get("primaryMarket", {})
        .get("scopeTotals", {})
        .get("aShareStockEtf", {})
        .get("flow1d")
    )
    if migrated_count != original_market_count:
        raise AssertionError(f"bootstrap migration changed A-share market ETF count: {original_market_count} -> {migrated_count}")
    if migrated_flow != migrated_primary:
        raise AssertionError(f"bootstrap migration market/primary mismatch: {migrated_flow} vs {migrated_primary}")
    if _finite(original_market_flow) and _finite(original_primary):
        if round(float(migrated_flow), 2) != round(float(original_market_flow), 2):
            raise AssertionError(f"bootstrap migration changed A-share 1d flow: {original_market_flow} -> {migrated_flow}")

    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", nargs="?", default=str(DEFAULT))
    args = parser.parse_args()
    path = Path(args.snapshot)
    snapshot = json.loads(path.read_text("utf-8"))
    if snapshot.get("dataContractVersion") == contract.CONTRACT_VERSION and snapshot.get("schemaVersion") == CLIENT_SNAPSHOT_SCHEMA_VERSION:
        print(f"snapshot already uses Data Contract {contract.CONTRACT_VERSION} / schema {CLIENT_SNAPSHOT_SCHEMA_VERSION}: {path}")
        return 0
    migrated = migrate(snapshot)
    text = json.dumps(migrated, ensure_ascii=False, indent=2)
    path.write_text(text, "utf-8")

    trade_date = str(migrated["tradeDate"])
    history = path.parent / "history" / f"{trade_date}.json"
    if history.exists():
        history.write_text(text, "utf-8")
    print(
        f"migrated verified snapshot {trade_date} to Data Contract {contract.CONTRACT_VERSION} / "
        f"schema {CLIENT_SNAPSHOT_SCHEMA_VERSION}; no new market facts were collected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Operational note: changes to this file intentionally retrigger the post-close publisher.
# Contents-API trigger: force one clean push event after the recovery fix landed.
# Recovery trigger: classification-claim audit is now covered by the real-snapshot CI test.