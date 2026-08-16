"""Resilient SSE source adapter used by the production ETF pipeline.

The upstream SSE interface has changed representation over time: older public
examples expose ``基金份额`` in 万份 while newer AKShare releases may already
return individual shares.  Treating the column as a permanently fixed unit can
create a 10,000x market-wide error, so the adapter validates the cross-sectional
magnitude and normalizes the old 万份 representation when it is unmistakable.

The maintained AKShare adapter is tried first; the legacy official SSE request
remains a fallback.  Both are still exchange-originated sources.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded

_ORIG_FETCH_SSE_SHARES = base.fetch_sse_shares

# Current individual-share cross sections have very large upper quantiles; old
# SSE/AKShare examples in 万份 are four orders of magnitude smaller.  Use the
# robust 90th percentile rather than a single flagship ETF.
_SSE_WAN_SHARE_P90_MAX = 100_000_000.0
_SSE_MIN_REASONABLE_P90_INDIVIDUAL = 100_000_000.0
_SSE_MAX_REASONABLE_P99_INDIVIDUAL = 10_000_000_000_000.0


def _normalize_share_units(values: pd.Series) -> tuple[pd.Series, str]:
    numeric = pd.to_numeric(values, errors="coerce")
    positive = numeric[numeric > 0].dropna()
    if positive.empty:
        raise ValueError("AKShare SSE ETF share response has no positive share observations")

    p90 = float(positive.quantile(0.90))
    unit = "shares"
    # Old exchange/API examples are in 万份.  A p90 below 1e8 is too small for a
    # full SSE ETF cross section in individual shares and safely identifies the
    # legacy representation.  Normalize exactly once.
    if p90 < _SSE_WAN_SHARE_P90_MAX:
        numeric = numeric * 10_000.0
        positive = numeric[numeric > 0].dropna()
        p90 = float(positive.quantile(0.90))
        unit = "wan_shares_scaled_10000"

    p99 = float(positive.quantile(0.99))
    if p90 < _SSE_MIN_REASONABLE_P90_INDIVIDUAL:
        raise ValueError(f"SSE ETF share unit unresolved: p90={p90:.2f}")
    if p99 > _SSE_MAX_REASONABLE_P99_INDIVIDUAL:
        raise ValueError(f"SSE ETF share unit implausibly large: p99={p99:.2f}")
    return numeric, unit


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
    out["基金份额"], unit = _normalize_share_units(out["基金份额"])
    out.attrs["share_unit_normalization"] = unit

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
