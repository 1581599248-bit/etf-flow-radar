from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import precision_cumulative_v7 as cumulative


class PrecisionCumulativeV7Tests(unittest.TestCase):
    DATES = ["2030-01-02", "2030-01-03", "2030-01-04", "2030-01-07", "2030-01-08"]
    DIGEST = "a" * 64

    def snapshot(self):
        return {
            "tradeDate": self.DATES[-1],
            "classificationRuleDigest": self.DIGEST,
            "market": {"primaryFlow1dYuanEstimate": 600_000.0, "flow1d": 0.01},
            "etfs": [{
                "code": "510300", "groupId": "hs300", "primaryFlow1dYuanEstimate": 600_000.0,
                "flow1d": 0.01,
            }],
            "groups": [{
                "id": "hs300", "primaryFlow1dYuanEstimate": 600_000.0, "flow1d": 0.01,
            }],
            "flowMetrics": {"primaryMarket": {"multiDay": {}}},
            "quality": {"cumulativeFlowHistory": {"officialSessionDates": list(self.DATES)}},
        }

    def write_daily(self, root: Path, stamp: str, digest: str | None = None, yuan: float = 600_000.0):
        daily = root / "daily"
        daily.mkdir(exist_ok=True)
        payload = {
            "schemaVersion": 2,
            "dataContractVersion": "7.0",
            "tradeDate": stamp,
            "metric": "primaryMarketNetSubscriptionEstimate",
            "valuation": "sameDayUnitNAV",
            "aggregationMethod1d": "sum_unrounded_share_delta_times_same_day_nav_then_round",
            "classificationRuleDigest": digest or self.DIGEST,
            "marketScopes": {
                "aShareStockEtf": {
                    "primaryFlow1dYuanEstimate": yuan,
                    "flow1d": round(yuan / 1e8, 2),
                }
            },
            "etfs": [{
                "code": "510300",
                "groupId": "hs300",
                "primaryFlow1dYuanEstimate": yuan,
                "flow1d": round(yuan / 1e8, 2),
            }],
        }
        (daily / f"{stamp}.json").write_text(json.dumps(payload), "utf-8")

    def test_five_day_cumulative_sums_yuan_before_rounding(self):
        snapshot = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp in self.DATES[:-1]:
                self.write_daily(root, stamp)
            with patch.object(cumulative.base, "PUBLIC", root):
                cumulative.apply(snapshot)

        # Each day's exact amount is 600,000 yuan = 0.006亿元. The published
        # daily display would be 0.01亿元, so adding display values would give
        # 0.05亿元. Exact-yuan five-day sum is 3,000,000 yuan = 0.03亿元.
        self.assertEqual(snapshot["market"]["flow5dCumulativeYuanEstimate"], 3_000_000.0)
        self.assertEqual(snapshot["market"]["flow5d"], 0.03)
        self.assertEqual(snapshot["groups"][0]["flow5d"], 0.03)
        self.assertEqual(snapshot["etfs"][0]["flow5dCumulative"], 0.03)
        self.assertEqual(snapshot["market"]["flow5dCumulativeStatus"], "available")
        self.assertEqual(snapshot["market"]["flow5dCumulativeSourceDates"], self.DATES)
        self.assertEqual(
            snapshot["flowMetrics"]["primaryMarket"]["multiDay"]["cumulative"]["aggregation"],
            "sumUnroundedDailyPrimaryFlowYuanThenRound",
        )

    def test_group_and_etf_cumulative_require_same_classification_digest(self):
        snapshot = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp in self.DATES[:-2]:
                self.write_daily(root, stamp)
            self.write_daily(root, self.DATES[-2], digest="b" * 64)
            with patch.object(cumulative.base, "PUBLIC", root):
                cumulative.apply(snapshot)

        # Market economics do not depend on the research taxonomy, so the exact
        # five-day market sum remains available. Group/ETF history cannot mix
        # different taxonomy contracts and is therefore withheld.
        self.assertEqual(snapshot["market"]["flow5d"], 0.03)
        self.assertEqual(snapshot["market"]["flow5dCumulativeStatus"], "available")
        self.assertIsNone(snapshot["groups"][0]["flow5d"])
        self.assertEqual(
            snapshot["groups"][0]["flow5dCumulativeStatus"],
            "insufficient_exact_yuan_same_classification_history",
        )
        self.assertIsNone(snapshot["etfs"][0]["flow5dCumulative"])

    def test_missing_exact_yuan_daily_fact_blocks_market_cumulative(self):
        snapshot = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp in self.DATES[:-2]:
                self.write_daily(root, stamp)
            # Deliberately omit one required date.
            with patch.object(cumulative.base, "PUBLIC", root):
                cumulative.apply(snapshot)

        self.assertIsNone(snapshot["market"]["flow5d"])
        self.assertIsNone(snapshot["market"]["flow5dCumulativeYuanEstimate"])
        self.assertEqual(snapshot["market"]["flow5dCumulativeSourceDates"], [])
        self.assertEqual(
            snapshot["market"]["flow5dCumulativeStatus"],
            "insufficient_exact_yuan_daily_history",
        )


if __name__ == "__main__":
    unittest.main()
