import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import capture_order_flow_v3 as capture


class CaptureOrderFlowCompatibilityTests(unittest.TestCase):
    def test_aggressor_imbalance_is_turnover_split_by_outer_inner_direction(self):
        day = date(2026, 8, 17)
        rows = 500
        spot = pd.DataFrame({
            "代码": [f"{510000 + i:06d}" for i in range(rows)],
            "名称": [f"ETF{i}" for i in range(rows)],
            "主力净流入-净额": [0.0] * rows,
            "成交额": [100_000_000.0] * rows,
            "外盘": [60.0] * rows,
            "内盘": [40.0] * rows,
            "数据日期": [day.isoformat()] * rows,
        })
        with patch.object(capture, "_is_exchange_session", return_value=True), patch.object(
            capture.base, "retry", return_value=spot
        ):
            payload = capture.build_snapshot(day)

        self.assertEqual(payload["metric"], "secondaryMarketAggressorImbalanceEstimate")
        self.assertEqual(payload["etfCount"], rows)
        self.assertEqual(payload["etfs"][0]["buyInitiatedEstimate1d"], 0.6)
        self.assertEqual(payload["etfs"][0]["sellInitiatedEstimate1d"], 0.4)
        self.assertEqual(payload["etfs"][0]["aggressorImbalance1d"], 0.2)
        self.assertEqual(payload["totalAggressorImbalance1d"], 100.0)
        self.assertIn("不代表市场净新增资金", payload["definition"])
        self.assertIn("不是ETF一级市场净申购/赎回", payload["definition"])


if __name__ == "__main__":
    unittest.main()
