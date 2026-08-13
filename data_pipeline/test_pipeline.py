import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_daily


class PipelineTests(unittest.TestCase):
    def test_classification_is_mutually_exclusive_and_blocks_non_a_share_assets(self):
        self.assertEqual(update_daily.classify_etf("沪深300ETF")["id"], "hs300")
        self.assertEqual(update_daily.classify_etf("沪深300红利ETF")["id"], "dividend")
        self.assertEqual(update_daily.classify_etf("半导体ETF")["kind"], "sector")
        self.assertIsNone(update_daily.classify_etf("港股通50ETF"))
        self.assertIsNone(update_daily.classify_etf("30年国债ETF"))

    def test_compatibility_index_mapping_avoids_partial_false_matches(self):
        self.assertEqual(update_daily.identify_index("沪深300ETF")[0], "000300")
        self.assertEqual(update_daily.identify_index("科创50ETF")[0], "000688")
        self.assertIsNone(update_daily.identify_index("港股通50ETF"))

    def test_percentile_requires_enough_history(self):
        self.assertIsNone(update_daily.percentile([1.0] * 59, 1.0))
        self.assertEqual(update_daily.percentile([1.0] * 60, 1.0), 100.0)

    def test_style_variants_do_not_enter_headline_benchmark_pool(self):
        self.assertTrue(update_daily.is_plain_benchmark("沪深300ETF"))
        self.assertFalse(update_daily.is_plain_benchmark("沪深300增强ETF"))
        self.assertFalse(update_daily.is_plain_benchmark("A500红利低波ETF"))

    def test_price_flow_state_covers_all_four_quadrants(self):
        self.assertEqual(update_daily._flow_state(1, 1), "上涨增配")
        self.assertEqual(update_daily._flow_state(-1, 1), "逆势承接")
        self.assertEqual(update_daily._flow_state(1, -1), "上涨减配")
        self.assertEqual(update_daily._flow_state(-1, -1), "下跌流出")

    def test_failed_snapshot_never_replaces_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            public = Path(temp)
            (public / "latest.json").write_text('{"safe": true}', "utf-8")
            with patch.object(update_daily, "PUBLIC", public):
                with self.assertRaises(RuntimeError):
                    update_daily.atomic_publish({"status": "failed", "tradeDate": "2026-08-11"})
            self.assertEqual(json.loads((public / "latest.json").read_text("utf-8")), {"safe": True})


if __name__ == "__main__":
    unittest.main()
