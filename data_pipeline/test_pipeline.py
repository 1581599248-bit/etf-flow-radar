import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import update_daily
import pandas as pd


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

    def test_sse_adapter_rejects_schema_drift(self):
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": [{"SEC_CODE": "510300"}]}
        with patch.object(update_daily.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                update_daily.fetch_sse_shares(date(2026, 8, 12))

    def test_sse_adapter_treats_empty_day_as_not_yet_published(self):
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": []}
        with patch.object(update_daily.requests, "get", return_value=response):
            frame = update_daily.fetch_sse_shares(date(2026, 8, 13))
        self.assertTrue(frame.empty)
        self.assertEqual(list(frame.columns), ["序号", "基金代码", "基金简称", "ETF类型", "统计日期", "基金份额"])

    def test_failed_snapshot_never_replaces_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            public = Path(temp)
            (public / "latest.json").write_text('{"safe": true}', "utf-8")
            with patch.object(update_daily, "PUBLIC", public):
                with self.assertRaises(RuntimeError):
                    update_daily.atomic_publish({"status": "failed", "tradeDate": "2026-08-11"})
            self.assertEqual(json.loads((public / "latest.json").read_text("utf-8")), {"safe": True})

    def test_default_refresh_checks_current_calendar_day_first(self):
        available = pd.DataFrame({"code": [str(i).zfill(6) for i in range(update_daily.MIN_MARKET_ETFS)]})
        calls = []
        def exchange(day):
            calls.append(day)
            if day == date(2026, 8, 13):
                raise ValueError("not published")
            return available
        with patch.object(update_daily, "fetch_exchange_shares", side_effect=exchange):
            day, frame = update_daily.fetch_available_shares(date(2026, 8, 13))
        self.assertEqual(calls, [date(2026, 8, 13), date(2026, 8, 12)])
        self.assertEqual(day, date(2026, 8, 12))
        self.assertEqual(len(frame), update_daily.MIN_MARKET_ETFS)


if __name__ == "__main__":
    unittest.main()
