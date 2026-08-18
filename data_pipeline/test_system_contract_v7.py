from __future__ import annotations

import unittest
from datetime import date, timedelta

import system_contract_v7 as contract


class UnifiedContractTests(unittest.TestCase):
    def snapshot(self):
        universe = [
            {
                "code": "510150", "name": "消费ETF招商", "shares": 1000.0, "nav": 1.0,
                "primaryFlow1d": 0.0, "shareDelta1d": 0.1, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": "sw_food_beverage",
            },
            {
                "code": "510300", "name": "沪深300ETF华泰柏瑞", "shares": 2000.0, "nav": 2.0,
                "primaryFlow1d": 0.000002, "shareDelta1d": 100.0, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": "hs300",
            },
            {
                "code": "510500", "name": "中证500ETF南方", "shares": 1500.0, "nav": 1.5,
                "primaryFlow1d": -0.00000075, "shareDelta1d": -50.0, "assetScope": "aShareStockEtf",
                "classificationStatus": "classified", "groupId": "csi500",
            },
        ]
        etfs = [
            {
                "code": "510150", "name": "消费ETF招商", "groupId": "sw_food_beverage", "kind": "industry",
                "assetScope": "aShareStockEtf", "shareDelta1d": 0.1, "flow1d": 0.0,
                "flow5d": 0.2, "flow20d": 0.5, "aum": 0.00001,
            },
            {
                "code": "510300", "name": "沪深300ETF华泰柏瑞", "groupId": "hs300", "kind": "broad",
                "assetScope": "aShareStockEtf", "shareDelta1d": 100.0, "flow1d": 0.000002,
                "flow5d": 0.3, "flow20d": 0.7, "aum": 0.00004,
            },
            {
                "code": "510500", "name": "中证500ETF南方", "groupId": "csi500", "kind": "broad",
                "assetScope": "aShareStockEtf", "shareDelta1d": -50.0, "flow1d": -0.00000075,
                "flow5d": -0.1, "flow20d": -0.4, "aum": 0.0000225,
            },
        ]
        groups = [
            {"id": "sw_food_beverage", "name": "食品饮料", "kind": "industry", "flow1d": 0.0, "flow5d": 0.2, "flow20d": 0.5, "aum": 0.00001, "representative": {"code": "510150", "name": "消费ETF招商"}},
            {"id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": 0.000002, "flow5d": 0.3, "flow20d": 0.7, "aum": 0.00004, "representative": {"code": "510300", "name": "沪深300ETF华泰柏瑞"}},
            {"id": "csi500", "name": "中证500", "kind": "broad", "flow1d": -0.00000075, "flow5d": -0.1, "flow20d": -0.4, "aum": 0.0000225, "representative": {"code": "510500", "name": "中证500ETF南方"}},
        ]
        scope = {
            "etfCount": 3, "flow1d": 0.0, "flow5dEndpoint": 0.4, "flow20dEndpoint": 0.8,
            "aum": 0.0000725, "increaseEtfCount1d": 2, "decreaseEtfCount1d": 1, "unchangedEtfCount1d": 0,
        }
        return {
            "tradeDate": "2030-01-08", "universe": universe, "etfs": etfs, "groups": groups,
            "market": dict(scope, flow5d=0.4, flow20d=0.8),
            "flowMetrics": {
                "primaryMarket": {
                    "metric": "primaryMarketNetSubscriptionEstimate", "valuation": "sameDayUnitNAV",
                    "scopeTotals": {"allEtf": dict(scope), "stockEtfIncludingCrossBorder": dict(scope), "aShareStockEtf": dict(scope)},
                    "assetClassTotals": {
                        "aShareStockEtf": dict(scope),
                        "crossBorderStockEtf": {"etfCount": 0, "flow1d": 0.0},
                        "bondEtf": {"etfCount": 0, "flow1d": 0.0},
                        "moneyEtf": {"etfCount": 0, "flow1d": 0.0},
                        "commodityEtf": {"etfCount": 0, "flow1d": 0.0},
                        "otherEtf": {"etfCount": 0, "flow1d": 0.0},
                    },
                },
                "secondaryMarketTradeFlow": {
                    "metric": "secondaryMarketTradeNetFlowEstimate", "scopeTotals": {"aShareStockEtf": {"netFlow1d": 1.0, "inflow1d": 10.0, "outflow1d": 9.0}},
                },
                "secondaryMarketOrderFlow": {"metric": "secondaryMarketMainOrderFlow"},
            },
            "quality": {}, "methodology": {},
        }

    def test_direction_is_canonical_across_market_and_asset_totals(self):
        snapshot = self.snapshot()
        contract.canonicalize_directions_and_totals(snapshot)
        market = snapshot["market"]
        asset = snapshot["flowMetrics"]["primaryMarket"]["assetClassTotals"]["aShareStockEtf"]
        self.assertEqual((market["increaseEtfCount1d"], market["decreaseEtfCount1d"], market["unchangedEtfCount1d"]), (1, 1, 1))
        self.assertEqual((asset["increaseEtfCount1d"], asset["decreaseEtfCount1d"], asset["unchangedEtfCount1d"]), (1, 1, 1))
        row = next(r for r in snapshot["universe"] if r["code"] == "510150")
        self.assertEqual(row["shareDirection1d"], "unchanged")
        self.assertEqual(row["shareDelta1d"], 0.0)

    def test_generic_consumption_name_is_not_asserted_as_food_beverage(self):
        snapshot = self.snapshot()
        contract.canonicalize_directions_and_totals(snapshot)
        contract.sanitize_classification(snapshot)
        self.assertNotIn("510150", {r["code"] for r in snapshot["etfs"]})
        row = next(r for r in snapshot["universe"] if r["code"] == "510150")
        self.assertEqual(row["classificationStatus"], "ambiguous")
        self.assertGreater(snapshot["quality"]["ambiguousClassificationCount"], 0)

    def test_incomplete_history_never_becomes_cumulative_flow(self):
        snapshot = self.snapshot()
        contract.canonicalize_directions_and_totals(snapshot)
        contract.sanitize_classification(snapshot)
        days = [date(2030, 1, 2) + timedelta(days=i) for i in range(7)]
        window = [(d, None) for d in days if d.weekday() < 5]
        # Force the final session label to match the synthetic snapshot date.
        window[-1] = (date(2030, 1, 8), None)
        contract.apply_cumulative_contract(snapshot, window)
        self.assertIsNone(snapshot["market"]["flow5d"])
        self.assertEqual(snapshot["market"]["flow5dCumulativeStatus"], "insufficient_verified_daily_history")
        self.assertIsNotNone(snapshot["market"]["flow5dEndpoint"])

    def test_secondary_trade_statistic_is_not_called_new_money(self):
        snapshot = self.snapshot()
        contract.harmonize_secondary_metrics(snapshot)
        trade = snapshot["flowMetrics"]["secondaryMarketTradeFlow"]
        self.assertEqual(trade["metric"], "secondaryMarketAggressorImbalanceEstimate")
        self.assertIn("不代表市场净新增资金", trade["definition"])
        self.assertIn("行情商", snapshot["flowMetrics"]["secondaryMarketOrderFlow"]["displayName"])


if __name__ == "__main__":
    unittest.main()
