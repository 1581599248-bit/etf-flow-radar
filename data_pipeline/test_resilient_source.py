import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import update_daily_resilient as resilient


class ResilientSseSourceTests(unittest.TestCase):
    def test_normalize_maintained_sse_schema_preserves_share_units(self):
        day = date(2026, 8, 14)
        raw = pd.DataFrame({
            "序号": [1, 2],
            "基金代码": ["510050", "588710"],
            "基金简称": ["50ETF", "科创半导体设备ETF"],
            "ETF类型": ["单市", "单市"],
            "统计日期": ["2026-08-14", "2026-08-14"],
            "基金份额": [7_146_666_800.0, 3_650_000_000.0],
        })
        out = resilient._normalize_akshare_sse(raw, day)
        self.assertEqual(float(out.loc[out["基金代码"] == "588710", "基金份额"].iloc[0]), 3_650_000_000.0)
        self.assertEqual(set(out["统计日期"]), {"2026-08-14"})

    def test_maintained_adapter_is_used_before_legacy_transport(self):
        day = date(2026, 8, 14)
        raw = pd.DataFrame({
            "序号": [1],
            "基金代码": ["510050"],
            "基金简称": ["50ETF"],
            "ETF类型": ["单市"],
            "统计日期": ["2026-08-14"],
            "基金份额": [7_146_666_800.0],
        })
        with patch.object(resilient.base.ak, "fund_etf_scale_sse", return_value=raw), patch.object(
            resilient, "_ORIG_FETCH_SSE_SHARES"
        ) as legacy:
            out = resilient.resilient_fetch_sse_shares(day)
        self.assertEqual(len(out), 1)
        legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
