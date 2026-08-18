from __future__ import annotations

import unittest
from unittest.mock import patch

import precision_contract_v7 as precision


class PrecisionContractTests(unittest.TestCase):
    def snapshot(self):
        universe = []
        etfs = []
        for index, code in enumerate(("510301", "510302", "510303"), start=1):
            universe.append({
                "code": code,
                "name": f"测试ETF{index}",
                "shares": 100_000_000.0,
                "shareDelta1d": 600_000.0,
                "shareDirection1d": "increase",
                "nav": 1.0,
                "primaryFlow1d": 0.01,  # legacy display-rounded amount in 亿元
                "assetScope": "aShareStockEtf",
                "classificationStatus": "classified",
                "groupId": "hs300",
            })
            etfs.append({
                "code": code,
                "name": f"测试ETF{index}",
                "groupId": "hs300",
                "kind": "broad",
                "shares": 100_000_000.0,
                "shareDelta1d": 600_000.0,
                "shareDirection1d": "increase",
                "nav": 1.0,
                "flow1d": 0.01,
                "primaryFlow1d": 0.01,
                "assetScope": "aShareStockEtf",
                "aum": 1.0,
                "shares5dAgoComparable": 99_000_000.0,
                "shareDelta5dEndpoint": 1_000_000.0,
                "flow5dEndpoint": 0.01,
                "shares20dAgoComparable": 95_000_000.0,
                "shareDelta20dEndpoint": 5_000_000.0,
                "flow20dEndpoint": 0.05,
            })
        scope = {
            "etfCount": 3,
            "flow1d": 0.03,  # wrong if summed from display-rounded ETF values
            "aum": 3.0,
            "increaseEtfCount1d": 3,
            "decreaseEtfCount1d": 0,
            "unchangedEtfCount1d": 0,
        }
        zero = {"etfCount": 0, "flow1d": 0.0}
        return {
            "universe": universe,
            "etfs": etfs,
            "groups": [{
                "id": "hs300", "name": "沪深300", "kind": "broad", "etfCount": 3,
                "flow1d": 0.03, "aum": 3.0,
                "flow5dEndpoint": 0.03, "flow20dEndpoint": 0.15,
            }],
            "market": dict(scope),
            "flowMetrics": {
                "primaryMarket": {
                    "scopeTotals": {
                        "allEtf": dict(scope),
                        "stockEtfIncludingCrossBorder": dict(scope),
                        "aShareStockEtf": dict(scope),
                    },
                    "assetClassTotals": {
                        "aShareStockEtf": dict(scope),
                        "crossBorderStockEtf": dict(zero),
                        "bondEtf": dict(zero),
                        "moneyEtf": dict(zero),
                        "commodityEtf": dict(zero),
                        "otherEtf": dict(zero),
                    },
                }
            },
            "quality": {"classifiedCoverageOfMarketPct": 100.0},
        }

    def test_aggregate_sums_unrounded_yuan_then_rounds_once(self):
        snapshot = self.snapshot()
        with patch.object(precision.production, "_build_industry_rollups", return_value=[]):
            precision.apply(snapshot)

        # Each ETF formula amount is 600,000 yuan = 0.006亿元. Displaying each
        # ETF at 0.01亿元 and then summing would incorrectly produce 0.03亿元.
        # Correct aggregate: 1,800,000 yuan = 0.018亿元 -> 0.02亿元.
        self.assertEqual(snapshot["market"]["primaryFlow1dYuanEstimate"], 1_800_000.0)
        self.assertEqual(snapshot["market"]["flow1d"], 0.02)
        self.assertEqual(
            snapshot["flowMetrics"]["primaryMarket"]["scopeTotals"]["aShareStockEtf"]["flow1d"],
            0.02,
        )
        self.assertEqual(snapshot["groups"][0]["flow1d"], 0.02)
        self.assertEqual(snapshot["groups"][0]["primaryFlow1dYuanEstimate"], 1_800_000.0)
        self.assertEqual(
            snapshot["quality"]["monetaryAggregationContract"],
            "sum_formula_amounts_in_yuan_before_any_display_rounding",
        )

    def test_largest_subscription_and_redemption_are_sign_aware(self):
        snapshot = self.snapshot()
        # Make one ETF a true redemption and one flat. Legacy max/min helpers
        # must not label a negative maximum as an inflow or a positive minimum as an outflow.
        snapshot["universe"][0]["shareDelta1d"] = -2_000_000.0
        snapshot["universe"][0]["shareDirection1d"] = "decrease"
        snapshot["etfs"][0]["shareDelta1d"] = -2_000_000.0
        snapshot["etfs"][0]["shareDirection1d"] = "decrease"
        snapshot["universe"][1]["shareDelta1d"] = 0.0
        snapshot["universe"][1]["shareDirection1d"] = "unchanged"
        snapshot["etfs"][1]["shareDelta1d"] = 0.0
        snapshot["etfs"][1]["shareDirection1d"] = "unchanged"

        with patch.object(precision.production, "_build_industry_rollups", return_value=[]):
            precision.apply(snapshot)

        market = snapshot["market"]
        self.assertEqual(market["largestNetSubscriptionEtf"]["code"], "510303")
        self.assertEqual(market["largestNetRedemptionEtf"]["code"], "510301")
        self.assertNotIn("topInflowEtf", market)
        self.assertNotIn("topOutflowEtf", market)
        self.assertIn("净申购", snapshot["conclusion"]["headline"])


if __name__ == "__main__":
    unittest.main()
