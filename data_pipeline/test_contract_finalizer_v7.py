from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import contract_finalizer_v7 as finalizer


class ContractFinalizerV7Tests(unittest.TestCase):
    def snapshot(self):
        return {
            "tradeDate": "2030-01-08",
            "universe": [
                {
                    "code": "510150", "name": "消费ETF招商", "classificationStatus": "ambiguous",
                    "groupId": "sw_food_beverage", "groupName": "食品饮料", "kind": "industry",
                },
                {
                    "code": "510300", "name": "沪深300ETF华泰柏瑞", "classificationStatus": "classified",
                    "groupId": "hs300", "groupName": "沪深300", "kind": "broad",
                },
            ],
            "etfs": [
                {
                    "code": "510300", "name": "沪深300ETF华泰柏瑞", "groupId": "hs300", "kind": "broad",
                    "flow1d": 2.0, "flow5d": 9.0, "flow20d": 30.0,
                    "flow5dEndpoint": 3.0, "flow20dEndpoint": 8.0,
                    "shareDelta5dEndpoint": 100.0, "shareDelta20dEndpoint": 200.0,
                    "aum": 100.0, "secondaryTradeNetFlow1d": 1.2, "secondaryMainOrderFlow1d": -0.4,
                },
                {
                    "code": "510310", "name": "沪深300ETF易方达", "groupId": "hs300", "kind": "broad",
                    "flow1d": -1.0, "flow5d": 4.0, "flow20d": 10.0,
                    "flow5dEndpoint": -1.0, "flow20dEndpoint": 2.0,
                    "shareDelta5dEndpoint": -50.0, "shareDelta20dEndpoint": 50.0,
                    "aum": 50.0,
                },
            ],
            "groups": [
                {
                    "id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": 1.0,
                    "flow5d": 13.0, "flow20d": 40.0, "flow5dEndpoint": 2.0, "flow20dEndpoint": 10.0,
                    "flowIntensity5dPct": 1.5, "relativeReturn20d": 2.0,
                    "representative": {"code": "599999", "name": "已失效代表ETF"},
                }
            ],
            "market": {
                "flow1d": 1.0, "flow5d": 20.0, "flow20d": 80.0,
                "flow5dEndpoint": 2.0, "flow20dEndpoint": 10.0,
            },
            "flowMetrics": {
                "primaryMarket": {},
                "secondaryMarketTradeFlow": {
                    "metric": "secondaryMarketAggressorImbalanceEstimate",
                    "status": "available",
                    "scopeTotals": {"aShareStockEtf": {"netFlow1d": 1.2, "inflow1d": 5.0, "outflow1d": 3.8}},
                },
                "secondaryMarketOrderFlow": {
                    "metric": "secondaryMarketMainOrderFlow",
                    "displayName": "行情商“主力净额”字段（交易统计）",
                    "status": "available",
                    "scopeTotals": {"aShareStockEtf": {"flow1d": -0.4}},
                },
            },
            "industryRollups": [
                {"id": "sw_electronics", "name": "电子", "flow1d": 1.0}
            ],
            "quality": {
                "cumulativeFlowHistory": {
                    "officialSessionDates": ["2030-01-02", "2030-01-03", "2030-01-04", "2030-01-07", "2030-01-08"]
                }
            },
        }

    def test_finalizer_removes_legacy_ambiguous_json_semantics(self):
        snapshot = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp, patch.object(finalizer.base, "PUBLIC", Path(tmp)):
            finalizer.finalize(snapshot)

        ambiguous = snapshot["universe"][0]
        self.assertEqual(ambiguous["candidateGroupId"], "sw_food_beverage")
        self.assertNotIn("groupId", ambiguous)
        self.assertNotIn("groupName", ambiguous)
        self.assertNotIn("kind", ambiguous)

        group = snapshot["groups"][0]
        self.assertEqual(group["representative"]["code"], "510300")
        self.assertIsNone(group["relativeReturn20d"])
        self.assertEqual(group["priceFlowState"], "数据待补")
        self.assertEqual(group["flow5dEndpoint"], 2.0)
        self.assertEqual(
            (group["increaseEtfCount5dEndpoint"], group["decreaseEtfCount5dEndpoint"], group["unchangedEtfCount5dEndpoint"]),
            (1, 1, 0),
        )
        self.assertNotIn("flowIntensity5dPct", group)
        self.assertEqual(group["flowIntensity5dEndpointPct"], 1.5)

        for row in snapshot["etfs"]:
            self.assertNotIn("flow5d", row)
            self.assertNotIn("flow20d", row)
            self.assertIsNone(row["flow5dCumulative"])
        self.assertEqual(snapshot["market"]["flow5dCumulativeStatus"], "insufficient_same_contract_daily_history")
        self.assertIsNone(snapshot["market"]["flow5d"])
        self.assertEqual(snapshot["market"]["multiDayMethod"], "sum_of_same_contract_verified_daily_primary_flows")

        metrics = snapshot["flowMetrics"]
        self.assertNotIn("secondaryMarketTradeFlow", metrics)
        self.assertNotIn("secondaryMarketOrderFlow", metrics)
        aggressor = metrics["secondaryMarketAggressorImbalance"]["scopeTotals"]["aShareStockEtf"]
        self.assertEqual(aggressor["aggressorImbalance1d"], 1.2)
        self.assertNotIn("netFlow1d", aggressor)
        vendor = metrics["secondaryMarketVendorMainOrder"]["scopeTotals"]["aShareStockEtf"]
        self.assertEqual(vendor["vendorMainOrderNet1d"], -0.4)
        self.assertNotIn("flow1d", vendor)
        self.assertEqual(snapshot["etfs"][0]["secondaryAggressorImbalance1d"], 1.2)
        self.assertEqual(snapshot["etfs"][0]["secondaryVendorMainOrderNet1d"], -0.4)

        self.assertNotIn("industryRollups", snapshot)
        self.assertIn("industryResearchRollups", snapshot)
        self.assertIn("研究汇总", snapshot["industryResearchRollups"][0]["classificationClaim"])

    def test_cumulative_requires_same_contract_and_same_classification_digest(self):
        snapshot = self.snapshot()
        digest = finalizer._classification_digest()
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            daily = public / "daily"
            daily.mkdir()
            # Four historical files deliberately omit Data Contract 7.0. They
            # must not be mixed with the current v7 snapshot even though dates exist.
            for stamp in ["2030-01-02", "2030-01-03", "2030-01-04", "2030-01-07"]:
                (daily / f"{stamp}.json").write_text(
                    '{"metric":"primaryMarketNetSubscriptionEstimate","valuation":"sameDayUnitNAV"}',
                    "utf-8",
                )
            with patch.object(finalizer.base, "PUBLIC", public):
                finalizer.finalize(snapshot)

        self.assertEqual(snapshot["classificationRuleDigest"], digest)
        self.assertIsNone(snapshot["market"]["flow5dCumulative"])
        self.assertEqual(snapshot["market"]["flow5dCumulativeSourceDates"], [])
        self.assertIsNone(snapshot["groups"][0]["flow5d"])


if __name__ == "__main__":
    unittest.main()
