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
        self.assertEqual(update_daily.classify_etf("半导体ETF")["kind"], "industry")
        self.assertEqual(update_daily.classify_etf("银行ETF")["id"], "sw_banks")
        self.assertEqual(update_daily.classify_etf("证券ETF")["id"], "fin_broker")
        self.assertEqual(update_daily.classify_etf("人工智能ETF")["id"], "comp_ai")
        self.assertIsNone(update_daily.classify_etf("港股通50ETF"))
        self.assertIsNone(update_daily.classify_etf("30年国债ETF"))
        self.assertIsNone(update_daily.classify_etf("黄金ETF华安"))
        self.assertIsNone(update_daily.classify_etf("招商快线ETF"))
        self.assertEqual(update_daily.classify_etf("黄金股ETF")["id"], "metal_gold")
        self.assertEqual(update_daily.classify_etf("半导体ETF")["id"], "elec_semiconductor")
        self.assertEqual(update_daily.classify_etf("芯片ETF")["id"], "elec_chip")
        self.assertEqual(update_daily.classify_etf("科创芯片ETF嘉实")["id"], "elec_chip")
        self.assertEqual(update_daily.classify_etf("半导体设备ETF广发")["id"], "elec_semiconductor")
        self.assertEqual(update_daily.classify_etf("集成电路ETF嘉实")["id"], "elec_semiconductor")
        self.assertEqual(update_daily.classify_etf("消费电子ETF")["id"], "elec_consumer")
        self.assertEqual(update_daily.classify_etf("卫星ETF")["id"], "comm_satellite")
        self.assertEqual(update_daily.classify_etf("卫星通信ETF")["id"], "comm_satellite")
        self.assertEqual(update_daily.classify_etf("通信ETF")["id"], "sw_communications")
        self.assertEqual(update_daily.classify_etf("券商ETF")["id"], "fin_broker")
        self.assertEqual(update_daily.classify_etf("光伏ETF")["id"], "power_pv")
        self.assertEqual(update_daily.classify_etf("创业板人工智能ETF大成")["id"], "comp_ai")
        self.assertEqual(update_daily.classify_etf("碳中和ETF南方")["id"], "carbon")
        self.assertEqual(update_daily.classify_etf("央企ETF")["id"], "soe")
        self.assertEqual(update_daily.classify_etf("A500富国")["id"], "csi_a500")
        self.assertIsNone(update_daily.classify_etf("纳斯达克100ETF大成"))
        self.assertIsNone(update_daily.classify_etf("金ETF天弘"))
        fallback = update_daily.classify_etf("全新策略指数ETF")
        self.assertEqual(fallback["id"], "other_a_share_stock_etf")
        self.assertEqual(fallback["kind"], "other")
        self.assertEqual(update_daily.classify_etf("自由现金流ETF")["id"], "free_cash_flow")
        self.assertEqual(update_daily.classify_etf("300现金", "沪深300自由现金流ETF汇添富")["id"], "free_cash_flow")

    def test_compatibility_index_mapping_avoids_partial_false_matches(self):
        self.assertEqual(update_daily.identify_index("沪深300ETF")[0], "000300")
        self.assertEqual(update_daily.identify_index("科创50ETF")[0], "000688")
        self.assertIsNone(update_daily.identify_index("港股通50ETF"))

    def test_percentile_requires_enough_history(self):
        self.assertIsNone(update_daily.percentile([1.0] * 59, 1.0))
        self.assertEqual(update_daily.percentile([1.0] * 60, 1.0), 100.0)

    def test_fast_publication_skips_nonessential_return_proxy_requests(self):
        representatives = [{"group_id": "hs300", "code": "510300", "name": "沪深300ETF", "exchange": "SSE"}]
        with patch.dict("os.environ", {"ETF_SKIP_RETURN_PROXIES": "1"}):
            self.assertEqual(
                update_daily.fetch_return_series(representatives, date(2026, 8, 1), date(2026, 8, 31)),
                {},
            )

    def test_style_variants_do_not_enter_headline_benchmark_pool(self):
        self.assertTrue(update_daily.is_plain_benchmark("沪深300ETF"))
        self.assertFalse(update_daily.is_plain_benchmark("沪深300增强ETF"))
        self.assertFalse(update_daily.is_plain_benchmark("A500红利低波ETF"))

    def test_broad_classification_uses_full_name_and_beats_manager_suffixes(self):
        self.assertEqual(update_daily.classify_etf("创业板ETF中银证券")["id"], "chinext")
        self.assertEqual(update_daily.classify_etf("A100", "A100ETF南方")["id"], "csi_a100")
        self.assertEqual(update_daily.classify_etf("深100ETF易方达")["id"], "szse100")
        self.assertEqual(update_daily.classify_etf("科创200E")["id"], "star200")

    def test_universe_audit_detects_added_missing_and_renamed(self):
        previous = pd.DataFrame([
            {"code": "510300", "name": "300ETF", "exchange": "SSE"},
            {"code": "159001", "name": "旧名称", "exchange": "SZSE"},
            {"code": "159999", "name": "已消失", "exchange": "SZSE"},
        ])
        current = pd.DataFrame([
            {"code": "510300", "name": "300ETF", "exchange": "SSE"},
            {"code": "159001", "name": "新名称", "exchange": "SZSE"},
            {"code": "159058", "name": "证券ETF大成", "exchange": "SZSE"},
        ])
        audit = update_daily.audit_universe(current, previous)
        self.assertEqual([row["code"] for row in audit["added"]], ["159058"])
        self.assertEqual([row["code"] for row in audit["missing"]], ["159999"])
        self.assertEqual(audit["renamed"][0]["previousName"], "旧名称")

    def test_price_flow_state_covers_all_four_quadrants(self):
        self.assertEqual(update_daily._flow_state(1, 1), "跑赢且流入")
        self.assertEqual(update_daily._flow_state(-1, 1), "跑输但流入")
        self.assertEqual(update_daily._flow_state(1, -1), "跑赢但流出")
        self.assertEqual(update_daily._flow_state(-1, -1), "跑输且流出")

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

    def test_warning_snapshot_never_replaces_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            public = Path(temp)
            (public / "latest.json").write_text('{"safe": true}', "utf-8")
            with patch.object(update_daily, "PUBLIC", public):
                with self.assertRaises(RuntimeError):
                    update_daily.atomic_publish({"status": "warning", "tradeDate": "2026-08-11"})
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
