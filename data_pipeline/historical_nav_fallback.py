"""Exact-date NAV recovery for ETF report rebuilds.

The Eastmoney on-exchange NAV feed used by the guarded pipeline is a realtime
post-close feed. It is useful on trade day, but it must not make a historical
rebuild impossible after that feed's publication window has passed.

This adapter keeps the existing guarded source as first choice. Only when that
source cannot provide the requested exact trade date do we fall back to
AKShare's Tonghuashun ETF category endpoint, which accepts an explicit date.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded


def _ths_exact_date_nav(day: date) -> pd.DataFrame:
    raw = base.retry(
        f"THS ETF exact-date NAV fallback {day.isoformat()}",
        lambda: base.ak.fund_etf_category_ths(symbol="ETF", date=day.strftime("%Y%m%d")),
        attempts=2,
    )
    required = {"基金代码", "基金名称", "当前-单位净值", "查询日期"}
    if raw.empty or not required.issubset(raw.columns):
        missing = sorted(required - set(raw.columns))
        raise ValueError(f"THS exact-date NAV fallback schema changed; missing={missing}")

    out = raw[["基金代码", "基金名称", "当前-单位净值", "查询日期"]].copy()
    out.columns = ["code", "price_name", "reference_price", "query_date"]
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["reference_price"] = pd.to_numeric(out["reference_price"], errors="coerce")
    out["query_date"] = pd.to_datetime(out["query_date"], errors="coerce").dt.date
    out = out[
        (out["query_date"] == day)
        & out["reference_price"].notna()
        & (out["reference_price"] > 0)
    ].drop_duplicates("code", keep="last")
    if out.empty:
        raise ValueError(f"THS exact-date NAV fallback has no rows for {day.isoformat()}")
    # Preserve the existing public schema: the valuation type is still NAV.
    out["reference_price_type"] = "NAV"
    return out[["code", "price_name", "reference_price", "reference_price_type"]]


def fetch_reference_prices(day: date) -> pd.DataFrame:
    """Prefer the existing guarded source; recover historical dates from THS."""
    try:
        return guarded.guarded_fetch_reference_prices(day)
    except Exception as realtime_error:
        print(
            f"[warn] realtime ETF NAV unavailable for {day.isoformat()} ({realtime_error}); "
            "falling back to THS exact-date NAV",
            file=base.sys.stderr,
        )
        try:
            return _ths_exact_date_nav(day)
        except Exception as historical_error:
            raise RuntimeError(
                f"no exact-date ETF NAV available for {day.isoformat()}; "
                f"realtime={realtime_error}; historical={historical_error}"
            ) from historical_error
