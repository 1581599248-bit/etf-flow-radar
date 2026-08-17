import unittest

import flow_scope_breakdown_v2 as scope


class FlowScopeBreakdownV2Tests(unittest.TestCase):
    def test_mutually_exclusive_asset_classes_reconcile_to_all_etf(self):
        snapshot = {
            "universe": [
                {"assetScope": "aShareStockEtf", "primaryFlow1d": 10.0, "shareDelta1d": 100_000_000.0, "nav": 10.0},
                {"assetScope": "crossBorderStockEtf", "primaryFlow1d": -2.0, "shareDelta1d": -100_000_000.0, "nav": 2.0},
                {"assetScope": "bondEtf", "primaryFlow1d": 3.0, "shareDelta1d": 100_000_000.0, "nav": 3.0},
                {"assetScope": "moneyEtf", "primaryFlow1d": -1.0, "shareDelta1d": -100_000_000.0, "nav": 1.0},
                {"assetScope": "commodityEtf", "primaryFlow1d": 0.5, "shareDelta1d": 100_000_000.0, "nav": 0.5},
                {"assetScope": "otherEtf", "primaryFlow1d": -0.5, "shareDelta1d": -100_000_000.0, "nav": 0.5},
            ],
            "flowMetrics": {"primaryMarket": {"scopeTotals": {"allEtf": {"flow1d": 10.0}}}},
        }
        scope.add_asset_class_totals(snapshot)
        primary = snapshot["flowMetrics"]["primaryMarket"]
        self.assertEqual(primary["assetClassTotals"]["aShareStockEtf"]["flow1d"], 10.0)
        self.assertEqual(primary["assetClassTotals"]["crossBorderStockEtf"]["flow1d"], -2.0)
        self.assertEqual(primary["assetClassReconciliation"]["sumOfMutuallyExclusiveAssetClasses"], 10.0)
        self.assertEqual(primary["assetClassReconciliation"]["difference"], 0.0)


if __name__ == "__main__":
    unittest.main()
