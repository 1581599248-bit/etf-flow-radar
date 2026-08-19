import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import update_daily_resilient as resilient


class ResilientSseSourceTests(unittest.TestCase):
    def test_normalize_maintained_sse_schema_preserves_individual_share_units(self):
        day = date(2026, 8, 14)
        raw = pd.DataFrame({
            "序号": list(range(1, 11)),
            "基金代码": [f"51{i:04d}" for i in range(10)],
            "基金简称": [f"ETF{i}" for i in range(10)],
            "ETF类型": ["单市"] * 10,
            "统计日期": ["2026-08-14"] * 10,
            "基金份额": [500_000_000.0 + i * 500_000_000.0 for i in range(10)],
        })
        out = resilient._normalize_akshare_sse(raw, day)
        self.assertEqual(float(out.iloc[-1]["基金份额"]), 5_000_000_000.0)
        self.assertEqual(out.attrs["share_unit_normalization"], "shares")
        self.assertEqual(set(out["统计日期"]), {"2026-08-14"})

    def test_legacy_wan_share_representation_is_scaled_once(self):
        day = date(2025, 1, 15)
        raw = pd.DataFrame({
            "序号": list(range(1, 11)),
            "基金代码": [f"51{i:04d}" for i in range(10)],
            "基金简称": [f"ETF{i}" for i in range(10)],
            "ETF类型": ["单市"] * 10,
            "统计日期": ["2025-01-15"] * 10,
            "基金份额": [50_000.0 + i * 50_000.0 for i in range(10)],
        })
        out = resilient._normalize_akshare_sse(raw, day)
        self.assertEqual(float(out.iloc[-1]["基金份额"]), 5_000_000_000.0)
        self.assertEqual(out.attrs["share_unit_normalization"], "wan_shares_scaled_10000")

    def test_maintained_adapter_is_used_before_legacy_transport(self):
        day = date(2026, 8, 14)
        raw = pd.DataFrame({
            "序号": list(range(1, 11)),
            "基金代码": [f"51{i:04d}" for i in range(10)],
            "基金简称": [f"ETF{i}" for i in range(10)],
            "ETF类型": ["单市"] * 10,
            "统计日期": ["2026-08-14"] * 10,
            "基金份额": [500_000_000.0 + i * 500_000_000.0 for i in range(10)],
        })
        # 浏览器通道是否真实联网取决于运行环境，必须 mock 掉，
        # 否则 SSE 可达的环境下测试会误用真实数据而失败。
        with patch.object(
            resilient, "_browser_session_sse_shares", side_effect=RuntimeError("browser transport down")
        ), patch.object(resilient.base.ak, "fund_etf_scale_sse", return_value=raw), patch.object(
            resilient, "_ORIG_FETCH_SSE_SHARES"
        ) as legacy:
            out = resilient.resilient_fetch_sse_shares(day)
        self.assertEqual(len(out), 10)
        legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
