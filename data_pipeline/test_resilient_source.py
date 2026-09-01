import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import update_daily_resilient as resilient


class ResilientSseSourceTests(unittest.TestCase):
    @staticmethod
    def _exchange_frame(day: date, shares: float) -> pd.DataFrame:
        return pd.DataFrame([{
            "code": "510300", "name": "沪深300ETF", "trade_date": day.isoformat(),
            "shares": shares, "exchange": "SSE",
        }])

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

    def test_recent_stale_exchange_cache_is_refreshed_before_build(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as arch_tmp, patch.object(
            resilient, "SHARE_CACHE_DIR", Path(tmp)
        ), patch.object(resilient, "SHARE_ARCHIVE_DIR", Path(arch_tmp)), patch.object(
            resilient.base, "MIN_MARKET_ETFS", 1
        ):
            resilient._RECENT_BUILD_SESSIONS.clear()
            resilient._write_exchange_cache(day, self._exchange_frame(day, 100.0))
            path = resilient._cache_path(day)
            payload = json.loads(path.read_text("utf-8"))
            payload.pop("fetchedAt")
            path.write_text(json.dumps(payload), "utf-8")
            with patch.object(
                resilient, "_ORIG_FETCH_EXCHANGE_SHARES",
                return_value=self._exchange_frame(day, 120.0),
            ) as live:
                out = resilient.resilient_fetch_exchange_shares(day)
            live.assert_called_once_with(day)
            self.assertEqual(float(out.iloc[0]["shares"]), 120.0)
            self.assertIn("fetchedAt", json.loads(path.read_text("utf-8")))

    def test_recent_fresh_exchange_cache_avoids_duplicate_live_request(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as arch_tmp, patch.object(
            resilient, "SHARE_CACHE_DIR", Path(tmp)
        ), patch.object(resilient, "SHARE_ARCHIVE_DIR", Path(arch_tmp)), patch.object(
            resilient.base, "MIN_MARKET_ETFS", 1
        ):
            resilient._RECENT_BUILD_SESSIONS.clear()
            resilient._write_exchange_cache(day, self._exchange_frame(day, 100.0))
            with patch.object(resilient, "_ORIG_FETCH_EXCHANGE_SHARES") as live:
                out = resilient.resilient_fetch_exchange_shares(day)
            live.assert_not_called()
            self.assertEqual(float(out.iloc[0]["shares"]), 100.0)

    def test_refresh_failure_falls_back_to_stored_cross_section(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as arch_tmp, patch.object(
            resilient, "SHARE_CACHE_DIR", Path(tmp)
        ), patch.object(resilient, "SHARE_ARCHIVE_DIR", Path(arch_tmp)), patch.object(
            resilient.base, "MIN_MARKET_ETFS", 1
        ):
            resilient._RECENT_BUILD_SESSIONS.clear()
            resilient._write_exchange_cache(day, self._exchange_frame(day, 100.0))
            path = resilient._cache_path(day)
            payload = json.loads(path.read_text("utf-8"))
            payload.pop("fetchedAt")
            path.write_text(json.dumps(payload), "utf-8")
            with patch.object(
                resilient, "_ORIG_FETCH_EXCHANGE_SHARES", side_effect=RuntimeError("SSE WAF ban 403")
            ):
                out = resilient.resilient_fetch_exchange_shares(day)
            self.assertEqual(float(out.iloc[0]["shares"]), 100.0)

    def test_durable_archive_covers_missing_volatile_cache(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as cache_tmp, tempfile.TemporaryDirectory() as arch_tmp, patch.object(
            resilient, "SHARE_CACHE_DIR", Path(cache_tmp)
        ), patch.object(resilient, "SHARE_ARCHIVE_DIR", Path(arch_tmp)), patch.object(
            resilient.base, "MIN_MARKET_ETFS", 1
        ):
            resilient._RECENT_BUILD_SESSIONS.clear()
            # Volatile cache file is absent; only the durable archive has this day.
            frame = self._exchange_frame(day, 100.0)
            path = resilient._archive_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schemaVersion": 1,
                "tradeDate": day.isoformat(),
                "fetchedAt": "2026-08-14T10:00:00+00:00",
                "source": "official_sse_szse_eod_shares",
                "rowCount": 1,
                "rows": frame.to_dict("records"),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            with patch.object(
                resilient, "_ORIG_FETCH_EXCHANGE_SHARES", side_effect=RuntimeError("SSE WAF ban 403")
            ):
                out = resilient.resilient_fetch_exchange_shares(day)
            self.assertEqual(float(out.iloc[0]["shares"]), 100.0)

    def test_write_mirrors_into_durable_archive(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as cache_tmp, tempfile.TemporaryDirectory() as arch_tmp, patch.object(
            resilient, "SHARE_CACHE_DIR", Path(cache_tmp)
        ), patch.object(resilient, "SHARE_ARCHIVE_DIR", Path(arch_tmp)), patch.object(
            resilient.base, "MIN_MARKET_ETFS", 1
        ):
            resilient._RECENT_BUILD_SESSIONS.clear()
            resilient._write_exchange_cache(day, self._exchange_frame(day, 100.0))
            self.assertTrue(resilient._cache_path(day).exists())
            self.assertTrue(resilient._archive_path(day).exists())
            payload = json.loads(resilient._archive_path(day).read_text("utf-8"))
            self.assertEqual(payload["tradeDate"], day.isoformat())
            self.assertEqual(payload["source"], "official_sse_szse_eod_shares")
            self.assertEqual(float(payload["rows"][0]["shares"]), 100.0)

    def test_cache_hit_backfills_durable_archive(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as arch_tmp, patch.object(
            resilient, "SHARE_CACHE_DIR", Path(tmp)
        ), patch.object(resilient, "SHARE_ARCHIVE_DIR", Path(arch_tmp)), patch.object(
            resilient.base, "MIN_MARKET_ETFS", 1
        ):
            resilient._RECENT_BUILD_SESSIONS.clear()
            resilient._write_exchange_cache(day, self._exchange_frame(day, 100.0))
            # Simulate the archive having been wiped while the volatile cache survives.
            resilient._archive_path(day).unlink()
            with patch.object(resilient, "_ORIG_FETCH_EXCHANGE_SHARES") as live:
                out = resilient.resilient_fetch_exchange_shares(day)
            live.assert_not_called()
            self.assertEqual(float(out.iloc[0]["shares"]), 100.0)
            self.assertTrue(resilient._archive_path(day).exists())


if __name__ == "__main__":
    unittest.main()
