from __future__ import annotations

import unittest
from unittest.mock import patch

import migrate_verified_snapshot_v7 as migration


class BootstrapMigrationTests(unittest.TestCase):
    def legacy_snapshot(self):
        rows = [
            ("510150", "消费ETF招商", "sw_food_beverage", "食品饮料", 0.1, 1.0, 0.0),
            ("510300", "沪深300ETF华泰柏瑞", "hs300", "沪深300", 100_000_000.0, 2.0, 2.0),
            ("510500", "中证500ETF南方", "csi500", "中证500", -66_666_666.67, 1.5, -1.0),
        ]
        universe, etfs = [], []
        for code, name, gid, gname, delta, nav, flow in rows:
            kind = "industry" if gid.startswith("sw_") else "broad"
            universe.append({
                "code": code, "name": name, "shares": 100_000_000.0, "nav": nav,
                "primaryFlow1d": flow, "shareDelta1d": delta, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": gid, "groupName": gname, "kind": kind,
            })
            etfs.append({
                "code": code, "name": name, "groupId": gid, "kind": kind,
                "assetScope": "aShareStockEtf", "shares": 100_000_000.0, "nav": nav,
                "shareDelta1d": delta, "flow1d": flow,
                "shares5dAgoComparable": 99_000_000.0, "shareDelta5dEndpoint": 1_000_000.0,
                "flow5d": 0.01, "flow5dEndpoint": 0.01,
                "shares20dAgoComparable": 95_000_000.0, "shareDelta20dEndpoint": 5_000_000.0,
                "flow20d": 0.05, "flow20dEndpoint": 0.05,
                "aum": nav,
            })

        groups = [
            {
                "id": "sw_food_beverage", "name": "食品饮料", "kind": "industry", "flow1d": 0.0,
                "flow5d": 0.01, "flow20d": 0.05, "flow5dEndpoint": 0.01, "flow20dEndpoint": 0.05,
                "aum": 1.0, "etfCount": 1, "representative": {"code": "510150", "name": "消费ETF招商"},
                "flowIntensity5dPct": 1.0, "relativeReturn20d": 1.0,
            },
            {
                "id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": 2.0,
                "flow5d": 0.01, "flow20d": 0.05, "flow5dEndpoint": 0.01, "flow20dEndpoint": 0.05,
                "aum": 2.0, "etfCount": 1, "representative": {"code": "510300", "name": "沪深300ETF华泰柏瑞"},
                "flowIntensity5dPct": 0.5, "relativeReturn20d": 0.0,
            },
            {
                "id": "csi500", "name": "中证500", "kind": "broad", "flow1d": -1.0,
                "flow5d": 0.01, "flow20d": 0.05, "flow5dEndpoint": 0.01, "flow20dEndpoint": 0.05,
                "aum": 1.5, "etfCount": 1, "representative": {"code": "510500", "name": "中证500ETF南方"},
                "flowIntensity5dPct": 0.7, "relativeReturn20d": -1.0,
            },
        ]
        scope = {
            "name": "A股股票ETF", "etfCount": 3, "etfCount5d": 3, "etfCount20d": 3,
            "flow1d": 1.0, "flow5dEndpoint": 0.03, "flow20dEndpoint": 0.15, "aum": 4.5,
            "increaseEtfCount1d": 2, "decreaseEtfCount1d": 1, "unchangedEtfCount1d": 0,
            "topInflowEtf": {"code": "510300", "name": "沪深300ETF华泰柏瑞", "flow1d": 2.0},
            "topOutflowEtf": {"code": "510500", "name": "中证500ETF南方", "flow1d": -1.0},
        }
        zero = {"etfCount": 0, "flow1d": 0.0, "increaseEtfCount1d": 0, "decreaseEtfCount1d": 0, "unchangedEtfCount1d": 0}
        return {
            "schemaVersion": 6,
            "sourceMode": "REAL",
            "status": "verified",
            "tradeDate": "2026-08-17",
            "generatedAt": "2026-08-18T02:33:36+08:00",
            "universe": universe,
            "etfs": etfs,
            "groups": groups,
            "market": dict(scope, flow5d=0.03, flow20d=0.15, multiDayMethod="endpoint_share_change_times_current_nav"),
            "flowMetrics": {
                "primaryMarket": {
                    "metric": "primaryMarketNetSubscriptionEstimate", "valuation": "sameDayUnitNAV",
                    "scopeTotals": {"allEtf": dict(scope), "stockEtfIncludingCrossBorder": dict(scope), "aShareStockEtf": dict(scope)},
                    "assetClassTotals": {
                        "aShareStockEtf": dict(scope), "crossBorderStockEtf": dict(zero), "bondEtf": dict(zero),
                        "moneyEtf": dict(zero), "commodityEtf": dict(zero), "otherEtf": dict(zero),
                    },
                },
                "secondaryMarketTradeFlow": {
                    "metric": "secondaryMarketTradeNetFlowEstimate", "status": "available",
                    "scopeTotals": {"aShareStockEtf": {"netFlow1d": -2.0, "inflow1d": 10.0, "outflow1d": 12.0}},
                },
                "secondaryMarketOrderFlow": {
                    "metric": "secondaryMarketMainOrderFlow", "status": "available",
                    "scopeTotals": {"aShareStockEtf": {"flow1d": -0.5}},
                },
            },
            "quality": {"officialSessions": 21},
            "methodology": {},
        }

    def test_migration_preserves_market_fact_and_uses_broad_research_theme(self):
        old = self.legacy_snapshot()
        with patch.object(migration.contract.production, "_build_industry_rollups", return_value=[]):
            migrated = migration.migrate(old)

        self.assertEqual(migrated["schemaVersion"], 7)
        self.assertEqual(migrated["dataContractVersion"], "7.0")
        self.assertEqual(migrated["tradeDate"], "2026-08-17")
        self.assertEqual(migrated["generatedAt"], "2026-08-18T02:33:36+08:00")
        self.assertEqual(migrated["market"]["flow1d"], 1.0)
        self.assertEqual(migrated["market"]["etfCount"], 3)
        self.assertIsNone(migrated["market"]["flow5d"])
        self.assertEqual(migrated["market"]["flow5dEndpoint"], 0.03)
        self.assertFalse(migrated["quality"]["contractMigration"]["newMarketFactsCollected"])
        self.assertTrue(migrated["quality"]["contractMigration"]["precisionAggregationApplied"])
        self.assertFalse(migrated["quality"]["contractMigration"]["trueMultiDayCumulativeBackfilled"])

        consumer = next(row for row in migrated["universe"] if row["code"] == "510150")
        self.assertEqual(consumer["classificationStatus"], "classified")
        self.assertEqual(consumer["groupId"], "theme_consumer")
        self.assertEqual(consumer["groupName"], "消费")
        client_consumer = next(row for row in migrated["etfs"] if row["code"] == "510150")
        self.assertEqual(client_consumer["groupId"], "theme_consumer")
        self.assertNotEqual(client_consumer["groupId"], "sw_food_beverage")

    def test_unverified_or_non_real_snapshot_is_rejected(self):
        for key, value in (("sourceMode", "MOCK"), ("status", "failed")):
            snapshot = self.legacy_snapshot()
            snapshot[key] = value
            with self.assertRaises(ValueError):
                migration.migrate(snapshot)


if __name__ == "__main__":
    unittest.main()
