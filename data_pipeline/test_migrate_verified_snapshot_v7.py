from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit_snapshot_v7 as audit_v7
import migrate_verified_snapshot_v7 as migration


class BootstrapMigrationTests(unittest.TestCase):
    def legacy_snapshot(self):
        universe = [
            {
                "code": "510150", "name": "消费ETF招商", "shares": 1000.0, "nav": 1.0,
                "primaryFlow1d": 0.0, "shareDelta1d": 0.1, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": "sw_food_beverage", "groupName": "食品饮料", "kind": "industry",
            },
            {
                "code": "510300", "name": "沪深300ETF华泰柏瑞", "shares": 2000.0, "nav": 2.0,
                "primaryFlow1d": 2.0, "shareDelta1d": 100000000.0, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": "hs300", "groupName": "沪深300", "kind": "broad",
            },
            {
                "code": "510500", "name": "中证500ETF南方", "shares": 1500.0, "nav": 1.5,
                "primaryFlow1d": -1.0, "shareDelta1d": -66666666.67, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": "csi500", "groupName": "中证500", "kind": "broad",
            },
        ]
        etfs = [
            {
                "code": "510150", "name": "消费ETF招商", "groupId": "sw_food_beverage", "kind": "industry",
                "assetScope": "aShareStockEtf", "shareDelta1d": 0.1, "flow1d": 0.0,
                "flow5d": 0.2, "flow20d": 0.5, "flow5dEndpoint": 0.2, "flow20dEndpoint": 0.5,
                "shareDelta5dEndpoint": 1.0, "shareDelta20dEndpoint": 1.0, "aum": 10.0,
            },
            {
                "code": "510300", "name": "沪深300ETF华泰柏瑞", "groupId": "hs300", "kind": "broad",
                "assetScope": "aShareStockEtf", "shareDelta1d": 100000000.0, "flow1d": 2.0,
                "flow5d": 3.0, "flow20d": 7.0, "flow5dEndpoint": 3.0, "flow20dEndpoint": 7.0,
                "shareDelta5dEndpoint": 150000000.0, "shareDelta20dEndpoint": 350000000.0, "aum": 40.0,
            },
            {
                "code": "510500", "name": "中证500ETF南方", "groupId": "csi500", "kind": "broad",
                "assetScope": "aShareStockEtf", "shareDelta1d": -66666666.67, "flow1d": -1.0,
                "flow5d": -0.5, "flow20d": -2.0, "flow5dEndpoint": -0.5, "flow20dEndpoint": -2.0,
                "shareDelta5dEndpoint": -33333333.33, "shareDelta20dEndpoint": -133333333.33, "aum": 30.0,
            },
        ]
        groups = [
            {
                "id": "sw_food_beverage", "name": "食品饮料", "kind": "industry", "flow1d": 0.0,
                "flow5d": 0.2, "flow20d": 0.5, "flow5dEndpoint": 0.2, "flow20dEndpoint": 0.5,
                "aum": 10.0, "etfCount": 1, "representative": {"code": "510150", "name": "消费ETF招商"},
                "flowIntensity5dPct": 2.0, "relativeReturn20d": 1.0,
            },
            {
                "id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": 2.0,
                "flow5d": 3.0, "flow20d": 7.0, "flow5dEndpoint": 3.0, "flow20dEndpoint": 7.0,
                "aum": 40.0, "etfCount": 1, "representative": {"code": "510300", "name": "沪深300ETF华泰柏瑞"},
                "flowIntensity5dPct": 7.5, "relativeReturn20d": 0.0,
            },
            {
                "id": "csi500", "name": "中证500", "kind": "broad", "flow1d": -1.0,
                "flow5d": -0.5, "flow20d": -2.0, "flow5dEndpoint": -0.5, "flow20dEndpoint": -2.0,
                "aum": 30.0, "etfCount": 1, "representative": {"code": "510500", "name": "中证500ETF南方"},
                "flowIntensity5dPct": -1.7, "relativeReturn20d": -1.0,
            },
        ]
        scope = {
            "name": "A股股票ETF", "etfCount": 3, "etfCount5d": 3, "etfCount20d": 3,
            "flow1d": 1.0, "flow5dEndpoint": 2.7, "flow20dEndpoint": 5.5, "aum": 80.0,
            "increaseEtfCount1d": 2, "decreaseEtfCount1d": 1, "unchangedEtfCount1d": 0,
            "topInflowEtf": {"code": "510300", "name": "沪深300ETF华泰柏瑞", "flow1d": 2.0},
            "topOutflowEtf": {"code": "510500", "name": "中证500ETF南方", "flow1d": -1.0},
        }
        zero_scope = {"etfCount": 0, "flow1d": 0.0, "increaseEtfCount1d": 0, "decreaseEtfCount1d": 0, "unchangedEtfCount1d": 0}
        return {
            "schemaVersion": 6,
            "sourceMode": "REAL",
            "status": "verified",
            "tradeDate": "2026-08-17",
            "generatedAt": "2026-08-18T02:33:36+08:00",
            "universe": universe,
            "etfs": etfs,
            "groups": groups,
            "market": dict(scope, flow5d=2.7, flow20d=5.5, multiDayMethod="endpoint_share_change_times_current_nav"),
            "flowMetrics": {
                "primaryMarket": {
                    "metric": "primaryMarketNetSubscriptionEstimate", "valuation": "sameDayUnitNAV",
                    "scopeTotals": {"allEtf": dict(scope), "stockEtfIncludingCrossBorder": dict(scope), "aShareStockEtf": dict(scope)},
                    "assetClassTotals": {
                        "aShareStockEtf": dict(scope), "crossBorderStockEtf": dict(zero_scope), "bondEtf": dict(zero_scope),
                        "moneyEtf": dict(zero_scope), "commodityEtf": dict(zero_scope), "otherEtf": dict(zero_scope),
                    },
                },
                "secondaryMarketTradeFlow": {
                    "metric": "secondaryMarketTradeNetFlowEstimate", "status": "available", "scopeTotals": {
                        "aShareStockEtf": {"netFlow1d": -2.0, "inflow1d": 10.0, "outflow1d": 12.0}
                    },
                },
                "secondaryMarketOrderFlow": {
                    "metric": "secondaryMarketMainOrderFlow", "status": "available", "scopeTotals": {
                        "aShareStockEtf": {"flow1d": -0.5}
                    },
                },
            },
            "quality": {"officialSessions": 21},
            "methodology": {},
        }

    def test_migration_preserves_trade_date_and_canonical_market_fact(self):
        old = self.legacy_snapshot()
        with patch.object(migration.contract.production, "_build_industry_rollups", return_value=[]):
            migrated = migration.migrate(old)

        self.assertEqual(migrated["dataContractVersion"], "7.0")
        self.assertEqual(migrated["tradeDate"], "2026-08-17")
        self.assertEqual(migrated["generatedAt"], "2026-08-18T02:33:36+08:00")
        self.assertEqual(migrated["market"]["flow1d"], 1.0)
        self.assertEqual(migrated["market"]["etfCount"], 3)
        self.assertIsNone(migrated["market"]["flow5d"])
        self.assertEqual(migrated["market"]["flow5dEndpoint"], 2.7)
        self.assertFalse(migrated["quality"]["contractMigration"]["newMarketFactsCollected"])
        self.assertFalse(migrated["quality"]["contractMigration"]["trueMultiDayCumulativeBackfilled"])

        ambiguous = next(row for row in migrated["universe"] if row["code"] == "510150")
        self.assertEqual(ambiguous["classificationStatus"], "ambiguous")
        self.assertNotIn("groupId", ambiguous)
        self.assertNotIn("510150", {row["code"] for row in migrated["etfs"]})

    def test_migration_preserves_pre_row_rounding_verified_aggregate(self):
        old = self.legacy_snapshot()
        # schema v6 aggregates were calculated from unrounded per-ETF values,
        # then individual JSON rows were rounded to 0.01亿元. Simulate the
        # resulting 0.01亿元 residual while leaving persisted ETF rows unchanged.
        old["market"]["flow1d"] = 1.01
        primary = old["flowMetrics"]["primaryMarket"]
        for key in ("allEtf", "stockEtfIncludingCrossBorder", "aShareStockEtf"):
            primary["scopeTotals"][key]["flow1d"] = 1.01
        primary["assetClassTotals"]["aShareStockEtf"]["flow1d"] = 1.01

        with patch.object(migration.contract.production, "_build_industry_rollups", return_value=[]):
            migrated = migration.migrate(old)

        self.assertEqual(migrated["market"]["flow1d"], 1.01)
        self.assertEqual(
            migrated["flowMetrics"]["primaryMarket"]["scopeTotals"]["aShareStockEtf"]["flow1d"],
            1.01,
        )
        residual = migrated["quality"]["bootstrapAggregatePrecision"]["scopeFlowRoundingResiduals"]["aShareStockEtf"]
        self.assertEqual(residual, 0.01)
        self.assertTrue(migrated["quality"]["contractMigration"]["preservedVerifiedAggregatePrecision"])

    def test_current_repository_snapshot_is_bootstrap_compatible_and_auditable(self):
        root = Path(__file__).resolve().parents[1]
        source_path = root / "site" / "data" / "latest.json"
        original = json.loads(source_path.read_text("utf-8"))
        original_trade_date = original.get("tradeDate")
        original_market_flow = original.get("market", {}).get("flow1d")
        original_market_count = original.get("market", {}).get("etfCount")

        migrated = migration.migrate(original)
        self.assertEqual(migrated.get("dataContractVersion"), migration.contract.CONTRACT_VERSION)
        self.assertEqual(migrated.get("schemaVersion"), migration.CLIENT_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(migrated.get("tradeDate"), original_trade_date)
        self.assertEqual(migrated.get("market", {}).get("flow1d"), original_market_flow)
        self.assertEqual(migrated.get("market", {}).get("etfCount"), original_market_count)

        with tempfile.TemporaryDirectory() as tmp:
            migrated_path = Path(tmp) / "latest.json"
            migrated_path.write_text(json.dumps(migrated, ensure_ascii=False), "utf-8")
            checks = audit_v7.audit(migrated_path)
        self.assertGreaterEqual(len(checks), 10)

    def test_unverified_or_non_real_snapshot_is_rejected(self):
        for key, value in (("sourceMode", "MOCK"), ("status", "failed")):
            snapshot = self.legacy_snapshot()
            snapshot[key] = value
            with self.assertRaises(ValueError):
                migration.migrate(snapshot)


if __name__ == "__main__":
    unittest.main()
