"""Production entrypoint for the ETF flow pipeline.

Adds an SSE transport fallback on top of ``update_daily_guarded``.  The legacy
pipeline hand-built the SSE request and GitHub-hosted runners can receive HTTP
403 from that endpoint.  AKShare now exposes the maintained official
``fund_etf_scale_sse`` interface, including historical dates and share units in
individual shares, so production uses that maintained adapter first and keeps
the legacy request only as a fallback.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded

_ORIG_FETCH_SSE_SHARES = base.fetch_sse_shares


def _normalize_akshare_sse(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    """Normalize AKShare's maintained SSE ETF-share interface to base schema."""
    required = {"基金代码", "基金简称", "统计日期", "基金份额"}
    if frame.empty:
        raise ValueError(f"{day.isoformat()} SSE closing shares have not been published")
    if not required.issubset(frame.columns):
        missing = sorted(required - set(frame.columns))
        raise ValueError(f"AKShare SSE ETF share schema changed; missing {missing}")

    out = frame.copy()
    if "序号" not in out.columns:
        out["序号"] = range(1, len(out) + 1)
    if "ETF类型" not in out.columns:
        out["ETF类型"] = ""
    out = out[["序号", "基金代码", "基金简称", "ETF类型", "统计日期", "基金份额"]].copy()
    out["基金代码"] = out["基金代码"].astype(str).str.zfill(6)
    out["统计日期"] = pd.to_datetime(out["统计日期"], errors="raise").dt.strftime("%Y-%m-%d")
    out["基金份额"] = pd.to_numeric(out["基金份额"], errors="coerce")
    if set(out["统计日期"].unique()) != {day.isoformat()}:
        raise ValueError("AKShare SSE ETF share date differs from request")
    if out["基金代码"].duplicated().any():
        raise ValueError("AKShare SSE ETF share response contains duplicate codes")
    if out[["基金代码", "基金简称", "基金份额"]].isna().any().any() or (out["基金份额"] < 0).any():
        raise ValueError("AKShare SSE ETF share response contains invalid rows")
    return out


def resilient_fetch_sse_shares(day: date) -> pd.DataFrame:
    """Use AKShare's maintained official SSE adapter before the legacy request."""
    stamp = day.strftime("%Y%m%d")
    maintained_error: Exception | None = None
    try:
        maintained = base.ak.fund_etf_scale_sse(date=stamp)
        return _normalize_akshare_sse(maintained, day)
    except Exception as exc:
        maintained_error = exc
        print(f"[warn] maintained SSE ETF share adapter failed: {exc}", file=base.sys.stderr)

    try:
        return _ORIG_FETCH_SSE_SHARES(day)
    except Exception as legacy_error:
        raise RuntimeError(
            "both SSE ETF-share transports failed; "
            f"maintained={maintained_error}; legacy={legacy_error}"
        ) from legacy_error


def install_resilient_sources() -> None:
    base.fetch_sse_shares = resilient_fetch_sse_shares
    guarded.install_guards()


def main() -> int:
    install_resilient_sources()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
