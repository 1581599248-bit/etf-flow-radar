import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import update_daily_v2 as v2


class UpdateDailyV2Tests(unittest.TestCase):
    def test_persisted_order_flow_is_preferred_over_current_spot(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            folder = public / "order_flow"
            folder.mkdir(parents=True)
            payload = {
                "schemaVersion": 1,
                "tradeDate": day.isoformat(),
                "metric": "secondaryMarketMainOrderFlow",
                "etfs": [
                    {"code": "510300", "name": "沪深300ETF华泰柏瑞", "mainOrderFlow1d": -2.5, "amount": 10.0}
                ],
            }
            (folder / f"{day.isoformat()}.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            with patch.object(v2.base, "PUBLIC", public), patch.object(v2.guarded, "_get_spot") as live:
                frame = v2._load_secondary_spot(day)
            live.assert_not_called()
            self.assertEqual(frame.loc[0, "代码"], "510300")
            self.assertEqual(frame.loc[0, "数据日期"], day.isoformat())
            self.assertEqual(float(frame.loc[0, "主力净流入-净额"]), -250_000_000.0)

    def test_missing_fact_file_falls_back_to_live_spot_with_downstream_date_guard(self):
        day = date(2026, 8, 14)
        live_frame = pd.DataFrame({"代码": ["510300"], "数据日期": ["2026-08-17"]})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(v2.base, "PUBLIC", Path(tmp)), patch.object(
                v2.guarded, "_get_spot", return_value=live_frame
            ) as live:
                frame = v2._load_secondary_spot(day)
        live.assert_called_once()
        self.assertEqual(frame.iloc[0]["数据日期"], "2026-08-17")


if __name__ == "__main__":
    unittest.main()
