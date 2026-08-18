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
- True 5d/20d cumulative flow is not backfilled from legacy endpoint fields.
- The migrated output must pass audit_snapshot_v7.py before workflows commit it.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import contract_finalizer_v7 as finalizer
import system_contract_v7 as contract

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "site" / "data" / "latest.json"
CN = ZoneInfo("Asia/Shanghai")
CLIENT_SNAPSHOT_SCHEMA_VERSION = 7


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

    # Apply only operations that can be derived from facts already persisted in
    # the verified snapshot. No exchange/NAV refetch is needed or allowed.
    contract.canonicalize_directions_and_totals(snapshot)
    contract.sanitize_classification(snapshot)
    contract.harmonize_secondary_metrics(snapshot)
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
    }

    # Hard invariants: the migration may change grouping breadth after removing
    # ambiguous names, but it must not change the canonical A-share market 1d
    # amount or market scope count.
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
    if original_market_flow is not None and original_primary is not None:
        # Old v6 market and primary totals should already agree; preserve that
        # economic fact to two-decimal published precision.
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
