import unittest

import flow_comparison_v2 as comparison


class FlowComparisonV2Tests(unittest.TestCase):
    def test_scope_comparisons_use_same_share_delta_not_a_second_flow_event(self):
        snapshot = {
            "universe": [
                {"code": "510300", "assetScope": "aShareStockEtf", "shareDelta1d": 100_000_000,
                 "referencePrice": 4.9},
                {"code": "513100", "assetScope": "crossBorderStockEtf", "shareDelta1d": -200_000_000,
                 "referencePrice": 2.0},
                {"code": "511010", "assetScope": "bondEtf", "shareDelta1d": 10_000_000,
                 "referencePrice": 100.0},
            ],
            "flowMetrics": {"primaryMarket": {}},
        }
        comparison.add_primary_valuation_comparisons(snapshot)
        values = snapshot["flowMetrics"]["primaryMarket"]["valuationComparisons"]
        self.assertEqual(values["canonical"], "sameDayUnitNAV")
        totals = values["alternatives"]["sameDayAverageTradedPrice"]["scopeTotals"]
        self.assertEqual(totals["aShareStockEtf"], {"etfCount": 1, "flow1d": 4.9})
        self.assertEqual(totals["stockEtfIncludingCrossBorder"], {"etfCount": 2, "flow1d": 0.9})
        self.assertEqual(totals["allEtf"], {"etfCount": 3, "flow1d": 10.9})


if __name__ == "__main__":
    unittest.main()
