import unittest
from unittest.mock import patch

import flow_scope_breakdown_v2 as scope


class FlowScopeBreakdownV2Tests(unittest.TestCase):
    def test_mutually_exclusive_asset_classes_reconcile_to_all_etf(self):
        snapshot = {
            "universe": [
                {"code": "510300", "assetScope": "aShareStockEtf", "primaryFlow1d": 10.0, "shareDelta1d": 100_000_000.0, "nav": 10.0},
                {"code": "513100", "assetScope": "crossBorderStockEtf", "primaryFlow1d": -2.0, "shareDelta1d": -100_000_000.0, "nav": 2.0},
                {"code": "511010", "assetScope": "bondEtf", "primaryFlow1d": 3.0, "shareDelta1d": 100_000_000.0, "nav": 3.0},
                {"code": "511880", "assetScope": "moneyEtf", "primaryFlow1d": -1.0, "shareDelta1d": -100_000_000.0, "nav": 1.0},
                {"code": "518880", "assetScope": "commodityEtf", "primaryFlow1d": 0.5, "shareDelta1d": 100_000_000.0, "nav": 0.5},
                {"code": "999999", "assetScope": "otherEtf", "primaryFlow1d": -0.5, "shareDelta1d": -100_000_000.0, "nav": 0.5},
            ],
            "etfs": [],
            "groups": [],
            "flowMetrics": {"primaryMarket": {"scopeTotals": {"allEtf": {"flow1d": 10.0}}}},
            "quality": {},
        }
        scope.add_asset_class_totals(snapshot)
        primary = snapshot["flowMetrics"]["primaryMarket"]
        self.assertEqual(primary["assetClassTotals"]["aShareStockEtf"]["flow1d"], 10.0)
        self.assertEqual(primary["assetClassTotals"]["crossBorderStockEtf"]["flow1d"], -2.0)
        self.assertEqual(primary["assetClassReconciliation"]["sumOfMutuallyExclusiveAssetClasses"], 10.0)
        self.assertEqual(primary["assetClassReconciliation"]["difference"], 0.0)

    def test_cross_border_etf_matching_a_theme_is_removed_from_client_group(self):
        snapshot = {
            "tradeDate": "2026-08-17",
            "windowStartDate": "2026-07-20",
            "universe": [
                {"code": "159869", "assetScope": "aShareStockEtf", "shareDelta1d": 10_000_000.0, "nav": 1.0},
                {"code": "517770", "assetScope": "crossBorderStockEtf", "shareDelta1d": 20_000_000.0, "nav": 1.0},
            ],
            "etfs": [
                {
                    "code": "159869", "name": "游戏ETF华夏", "exchange": "SZSE",
                    "groupId": "media_game", "flow1d": 0.1, "flow5dEndpoint": 0.2,
                    "flow20dEndpoint": 0.3, "shareDelta1d": 10_000_000.0,
                    "shareDelta5dEndpoint": 20_000_000.0, "previousComparableShares": 100_000_000.0,
                    "shares5dAgoComparable": 90_000_000.0, "shares20dAgoComparable": 80_000_000.0,
                    "nav": 1.0, "aum": 1.1,
                },
                {
                    "code": "517770", "name": "游戏传媒ETF浦银", "exchange": "SSE",
                    "groupId": "media_game", "flow1d": 0.2, "flow5dEndpoint": 0.4,
                    "flow20dEndpoint": 0.6, "shareDelta1d": 20_000_000.0,
                    "shareDelta5dEndpoint": 40_000_000.0, "previousComparableShares": 100_000_000.0,
                    "shares5dAgoComparable": 80_000_000.0, "shares20dAgoComparable": 60_000_000.0,
                    "nav": 1.0, "aum": 1.2,
                },
            ],
            "groups": [
                {
                    "id": "media_game", "name": "游戏", "kind": "industry",
                    "flow1d": 0.3, "flow5d": 0.6, "flow20d": 0.9,
                    "flowIntensity5dPct": 1.0, "relativeReturn20d": 2.0,
                    "representative": {"code": "159869", "name": "游戏ETF华夏"},
                }
            ],
            "flowMetrics": {"primaryMarket": {"scopeTotals": {"allEtf": {"flow1d": 0.3}}}},
            "quality": {},
        }
        scope.add_asset_class_totals(snapshot)
        self.assertEqual([row["code"] for row in snapshot["etfs"]], ["159869"])
        self.assertEqual(snapshot["etfs"][0]["assetScope"], "aShareStockEtf")
        self.assertEqual(snapshot["groups"][0]["flow1d"], 0.1)
        self.assertEqual(snapshot["groups"][0]["etfCount"], 1)
        guard = snapshot["quality"]["classifiedAshareScopeEnforcement"]
        self.assertEqual(guard["beforeCount"], 2)
        self.assertEqual(guard["afterCount"], 1)
        self.assertEqual(guard["excludedCount"], 1)
        self.assertEqual(guard["excludedByScope"], {"crossBorderStockEtf": 1})

    def test_cross_border_representative_is_replaced_before_client_use(self):
        snapshot = {
            "tradeDate": "2026-08-17",
            "windowStartDate": "2026-07-20",
            "universe": [
                {"code": "159869", "assetScope": "aShareStockEtf", "shareDelta1d": 0.0, "nav": 1.0},
                {"code": "517770", "assetScope": "crossBorderStockEtf", "shareDelta1d": 0.0, "nav": 1.0},
            ],
            "etfs": [
                {
                    "code": "159869", "name": "游戏ETF华夏", "exchange": "SZSE",
                    "groupId": "media_game", "flow1d": 0.0, "flow5dEndpoint": 0.0,
                    "flow20dEndpoint": 0.0, "shareDelta1d": 0.0, "shareDelta5dEndpoint": 0.0,
                    "previousComparableShares": 100_000_000.0, "shares5dAgoComparable": 100_000_000.0,
                    "shares20dAgoComparable": 100_000_000.0, "nav": 1.0, "aum": 2.0,
                },
                {
                    "code": "517770", "name": "游戏传媒ETF浦银", "exchange": "SSE",
                    "groupId": "media_game", "flow1d": 0.0, "flow5dEndpoint": 0.0,
                    "flow20dEndpoint": 0.0, "shareDelta1d": 0.0, "shareDelta5dEndpoint": 0.0,
                    "previousComparableShares": 100_000_000.0, "shares5dAgoComparable": 100_000_000.0,
                    "shares20dAgoComparable": 100_000_000.0, "nav": 1.0, "aum": 3.0,
                },
            ],
            "groups": [
                {
                    "id": "media_game", "name": "游戏", "kind": "industry",
                    "flow1d": 0.0, "flow5d": 0.0, "flow20d": 0.0,
                    "flowIntensity5dPct": 0.0, "relativeReturn20d": 5.0,
                    "representative": {"code": "517770", "name": "游戏传媒ETF浦银"},
                }
            ],
            "flowMetrics": {"primaryMarket": {"scopeTotals": {"allEtf": {"flow1d": 0.0}}}},
            "quality": {},
        }
        with patch.object(scope.base, "fetch_return_series", return_value={}):
            scope.add_asset_class_totals(snapshot)
        group = snapshot["groups"][0]
        self.assertEqual(group["representative"]["code"], "159869")
        self.assertIsNone(group["return20d"])
        self.assertEqual(group["priceFlowState"], "待补充")
        self.assertEqual(snapshot["quality"]["classifiedAshareScopeEnforcement"]["returnRepresentativeRefreshCount"], 1)


if __name__ == "__main__":
    unittest.main()
