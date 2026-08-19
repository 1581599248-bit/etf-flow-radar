"""Resilient SSE source adapter used by the production ETF pipeline.

The upstream SSE interface has changed representation over time: older public
examples expose ``基金份额`` in 万份 while newer AKShare releases may already
return individual shares. Treating the column as a permanently fixed unit can
create a 10,000x market-wide error, so the adapter validates the cross-sectional
magnitude and normalizes the old 万份 representation when it is unmistakable.

The maintained AKShare adapter is tried first. If the exchange rejects that
transport from a hosted runner, a browser-session request to the same official
SSE endpoint is tried with the current ETF-scale page as the referer and a
smaller page size. The legacy official SSE request remains the final fallback.
All transports remain exchange-originated sources.
"""
from __future__ import annotations

import json
import time
from datetime import date

import pandas as pd
import requests

import update_daily as base
import update_daily_guarded as guarded
import historical_nav_fallback

_ORIG_FETCH_SSE_SHARES = base.fetch_sse_shares

SSE_SCALE_PAGE = "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_SCALE_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"

# Current individual-share cross sections have very large upper quantiles; old
# SSE/AKShare examples in 万份 are four orders of magnitude smaller. Use the
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
    # Old exchange/API examples are in 万份. A p90 below 1e8 is too small for a
    # full SSE ETF cross section in individual shares and safely identifies the
    # legacy representation. Normalize exactly once.
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
    """Normalize an SSE ETF-share frame to the production base schema."""
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


def _extract_sse_payload(response: requests.Response) -> dict:
    """Parse JSON or JSONP from the official SSE query response."""
    text = response.text.strip()
    try:
        payload = response.json()
    except ValueError:
        left = text.find("{")
        right = text.rfind("}")
        if left < 0 or right <= left:
            preview = " ".join(text[:160].split())
            raise ValueError(
                f"official SSE browser transport returned non-JSON content; "
                f"status={response.status_code}; preview={preview!r}"
            )
        payload = json.loads(text[left : right + 1])
    if not isinstance(payload, dict):
        raise ValueError("official SSE browser transport returned a non-object payload")
    return payload


def _browser_session_sse_shares(day: date) -> pd.DataFrame:
    """Fetch the same official SSE dataset with a browser-like primed session."""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": SSE_SCALE_PAGE,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    })
    # Prime cookies through the public scale page. Failure here is non-fatal;
    # some SSE edges do not set cookies while the query endpoint still works.
    try:
        session.get(SSE_SCALE_PAGE, timeout=12)
    except requests.RequestException:
        pass

    params = {
        "isPagination": "true",
        "pageHelp.pageSize": "1000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": SSE_SCALE_SQL_ID,
        "STAT_DATE": day.isoformat(),
        "_": str(int(time.time() * 1000)),
    }
    response = session.get(SSE_QUERY_URL, params=params, timeout=20)
    response.raise_for_status()
    payload = _extract_sse_payload(response)
    rows = payload.get("result")
    if not isinstance(rows, list):
        rows = (payload.get("pageHelp") or {}).get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{day.isoformat()} official SSE browser transport returned no ETF share rows")

    frame = pd.DataFrame(rows).rename(columns={
        "NUM": "序号",
        "SEC_CODE": "基金代码",
        "SEC_NAME": "基金简称",
        "ETF_TYPE": "ETF类型",
        "STAT_DATE": "统计日期",
        "TOT_VOL": "基金份额",
    })
    return _normalize_akshare_sse(frame, day)


def resilient_fetch_sse_shares(day: date) -> pd.DataFrame:
    """Try three official SSE transports without substituting third-party shares."""
    stamp = day.strftime("%Y%m%d")
    maintained_error: Exception | None = None
    browser_error: Exception | None = None
    try:
        maintained = base.ak.fund_etf_scale_sse(date=stamp)
        return _normalize_akshare_sse(maintained, day)
    except Exception as exc:
        maintained_error = exc
        print(f"[warn] maintained SSE ETF share adapter failed: {exc}", file=base.sys.stderr)

    try:
        return _browser_session_sse_shares(day)
    except Exception as exc:
        browser_error = exc
        print(f"[warn] browser-session SSE ETF share adapter failed: {exc}", file=base.sys.stderr)

    try:
        return _ORIG_FETCH_SSE_SHARES(day)
    except Exception as legacy_error:
        raise RuntimeError(
            "all SSE ETF-share transports failed; "
            f"maintained={maintained_error}; browser={browser_error}; legacy={legacy_error}"
        ) from legacy_error


def install_resilient_sources() -> None:
    base.fetch_sse_shares = resilient_fetch_sse_shares
    guarded.install_guards()
    # The guarded source is a same-day realtime feed. Keep it first, but make
    # historical rebuilds recoverable after that feed's publication window.
    base.fetch_reference_prices = historical_nav_fallback.fetch_reference_prices


def main() -> int:
    install_resilient_sources()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
