"""Resilient official-exchange source adapters for the ETF production pipeline.

The client-facing methodology is unchanged: SSE/SZSE end-of-day shares remain
the authoritative share source.  This layer only makes transport more reliable.

Two rules matter operationally:
1. A validated historical exchange cross-section is immutable for our research
   purpose, so cache it locally and never redownload the same 21-day history on
   every daily build.
2. SSE browser transport reuses one primed session and is deliberately
   rate-limited.  403/429 responses back off and refresh the session instead of
   hammering the exchange endpoint.

The cache is transport-only.  It never substitutes vendor shares and it is read
back through the same date/schema/count validation used for live official data.
"""
from __future__ import annotations

import json
import random
import socket
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import update_daily as base
import update_daily_guarded as guarded
import historical_nav_fallback

_ORIG_FETCH_SSE_SHARES = base.fetch_sse_shares
_ORIG_FETCH_EXCHANGE_SHARES = base.fetch_exchange_shares

SSE_SCALE_PAGE = "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_SCALE_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"

# GitHub Actions restores this directory between runs.  It is intentionally not
# under site/data, so transport cache files are never published to clients.
SHARE_CACHE_DIR = base.ROOT / ".cache" / "exchange_shares"

# Current individual-share cross sections have very large upper quantiles; old
# SSE/AKShare examples in 万份 are four orders of magnitude smaller. Use the
# robust 90th percentile rather than a single flagship ETF.
_SSE_WAN_SHARE_P90_MAX = 100_000_000.0
_SSE_MIN_REASONABLE_P90_INDIVIDUAL = 100_000_000.0
_SSE_MAX_REASONABLE_P99_INDIVIDUAL = 10_000_000_000_000.0

_SSE_SESSION: requests.Session | None = None
_SSE_LAST_REQUEST_AT = 0.0
_SSE_MIN_REQUEST_INTERVAL = 1.25

# Hosted CI sometimes receives an unusable IPv6 route for query.sse.com.cn.
# Keep every resolved address, but try IPv4 first so urllib3 can fall back to
# IPv6 only when it is actually reachable.
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_IPV4_PREFERENCE_INSTALLED = False


def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = list(_ORIGINAL_GETADDRINFO(*args, **kwargs))
    return sorted(results, key=lambda item: 0 if item[0] == socket.AF_INET else 1)


def install_ipv4_preference() -> None:
    global _IPV4_PREFERENCE_INSTALLED
    if _IPV4_PREFERENCE_INSTALLED:
        return
    socket.getaddrinfo = _ipv4_first_getaddrinfo
    _IPV4_PREFERENCE_INSTALLED = True

# The SSE WAF hands out 403/429 bans that last minutes, not seconds.  Short
# backoffs just extend the ban, so wait long enough for the ban window to
# lapse between attempts.  Retry-After, when present, takes precedence.
_SSE_BAN_BACKOFF_SECONDS = (30.0, 90.0, 180.0)
_SSE_BAN_BACKOFF_CAP = 300.0
_SSE_ERROR_BACKOFF_SECONDS = (5.0, 15.0, 40.0)


def _normalize_share_units(values: pd.Series) -> tuple[pd.Series, str]:
    numeric = pd.to_numeric(values, errors="coerce")
    positive = numeric[numeric > 0].dropna()
    if positive.empty:
        raise ValueError("AKShare SSE ETF share response has no positive share observations")

    p90 = float(positive.quantile(0.90))
    unit = "shares"
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


def _new_sse_session() -> requests.Session:
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
        "Connection": "keep-alive",
    })
    try:
        session.get(SSE_SCALE_PAGE, timeout=12)
    except requests.RequestException:
        pass
    return session


def _get_sse_session(*, refresh: bool = False) -> requests.Session:
    global _SSE_SESSION
    if refresh and _SSE_SESSION is not None:
        try:
            _SSE_SESSION.close()
        except Exception:
            pass
        _SSE_SESSION = None
    if _SSE_SESSION is None:
        _SSE_SESSION = _new_sse_session()
    return _SSE_SESSION


def _throttle_sse() -> None:
    global _SSE_LAST_REQUEST_AT
    elapsed = time.monotonic() - _SSE_LAST_REQUEST_AT
    if _SSE_LAST_REQUEST_AT and elapsed < _SSE_MIN_REQUEST_INTERVAL:
        time.sleep(_SSE_MIN_REQUEST_INTERVAL - elapsed)
    _SSE_LAST_REQUEST_AT = time.monotonic()


def _ban_backoff(attempt: int, response: requests.Response) -> float:
    """Seconds to wait after a 403/429 ban; honors Retry-After when present."""
    delay = _SSE_BAN_BACKOFF_SECONDS[min(attempt, len(_SSE_BAN_BACKOFF_SECONDS)) - 1]
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after.isdigit():
        delay = max(delay, min(float(retry_after), _SSE_BAN_BACKOFF_CAP))
    # Small jitter so concurrent serialized runs do not retry in lockstep.
    return delay + random.uniform(0.0, 5.0)


def _browser_session_sse_shares(day: date) -> pd.DataFrame:
    """Fetch one exact official SSE date using a persistent browser session."""
    params = {
        "isPagination": "true",
        "pageHelp.pageSize": "1000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": SSE_SCALE_SQL_ID,
        "STAT_DATE": day.isoformat(),
    }
    errors: list[str] = []
    for attempt in range(1, 5):
        session = _get_sse_session(refresh=attempt > 1)
        params["_"] = str(int(time.time() * 1000))
        try:
            _throttle_sse()
            response = session.get(SSE_QUERY_URL, params=params, timeout=25)
            if response.status_code in {403, 429}:
                errors.append(f"attempt {attempt}: HTTP {response.status_code}")
                if attempt < 4:
                    delay = _ban_backoff(attempt, response)
                    print(
                        f"[warn] SSE WAF ban (HTTP {response.status_code}); "
                        f"backing off {delay:.0f}s before attempt {attempt + 1}",
                        file=base.sys.stderr,
                    )
                    time.sleep(delay)
                    continue
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
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < 4:
                time.sleep(_SSE_ERROR_BACKOFF_SECONDS[attempt - 1] + random.uniform(0.0, 3.0))
    raise RuntimeError("; ".join(errors[-4:]))


def resilient_fetch_sse_shares(day: date) -> pd.DataFrame:
    """Use official SSE transports while minimizing repeated requests."""
    browser_error: Exception | None = None
    maintained_error: Exception | None = None

    # Hosted runners have recently been more reliable with the browser-session
    # form than AKShare's fresh request for every date, so use one shared session
    # first.  Both hit the same official SSE dataset.
    try:
        return _browser_session_sse_shares(day)
    except Exception as exc:
        browser_error = exc
        print(f"[warn] browser-session SSE ETF share adapter failed: {exc}", file=base.sys.stderr)

    stamp = day.strftime("%Y%m%d")
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
            "all SSE ETF-share transports failed; "
            f"browser={browser_error}; maintained={maintained_error}; legacy={legacy_error}"
        ) from legacy_error


def _cache_path(day: date) -> Path:
    return SHARE_CACHE_DIR / f"{day.isoformat()}.json"


def _validate_exchange_frame(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    required = {"code", "name", "trade_date", "shares", "exchange"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError("cached official exchange share frame is incomplete")
    out = frame[["code", "name", "trade_date", "shares", "exchange"]].copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.strftime("%Y-%m-%d")
    out["shares"] = pd.to_numeric(out["shares"], errors="coerce")
    if set(out["trade_date"].unique()) != {day.isoformat()}:
        raise ValueError("cached official exchange share date differs from request")
    if len(out) < base.MIN_MARKET_ETFS:
        raise ValueError(f"cached official exchange share frame has only {len(out)} rows")
    if out["code"].duplicated().any():
        raise ValueError("cached official exchange share frame contains duplicate codes")
    if out[["code", "name", "shares", "exchange"]].isna().any().any() or (out["shares"] < 0).any():
        raise ValueError("cached official exchange share frame contains invalid rows")
    if not set(out["exchange"].astype(str)).issubset({"SSE", "SZSE"}):
        raise ValueError("cached official exchange share frame has unknown exchange")
    return out


def _read_exchange_cache(day: date) -> pd.DataFrame | None:
    path = _cache_path(day)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("tradeDate") != day.isoformat() or payload.get("source") != "official_sse_szse_eod_shares":
            raise ValueError("cache metadata mismatch")
        frame = pd.DataFrame(payload.get("rows", []))
        return _validate_exchange_frame(frame, day)
    except Exception as exc:
        print(f"[warn] ignoring invalid exchange-share cache {path.name}: {exc}", file=base.sys.stderr)
        return None


def _write_exchange_cache(day: date, frame: pd.DataFrame) -> None:
    validated = _validate_exchange_frame(frame, day)
    SHARE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "code": str(row.code),
            "name": str(row.name),
            "trade_date": str(row.trade_date),
            "shares": float(row.shares),
            "exchange": str(row.exchange),
        }
        for row in validated.itertuples(index=False)
    ]
    payload = {
        "schemaVersion": 1,
        "tradeDate": day.isoformat(),
        "source": "official_sse_szse_eod_shares",
        "rowCount": len(rows),
        "rows": rows,
    }
    path = _cache_path(day)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def resilient_fetch_exchange_shares(day: date) -> pd.DataFrame:
    """Read immutable verified history from cache; fetch only a missing date."""
    cached = _read_exchange_cache(day)
    if cached is not None:
        print(f"official share cache hit: {day}", flush=True)
        return cached
    frame = _ORIG_FETCH_EXCHANGE_SHARES(day)
    validated = _validate_exchange_frame(frame, day)
    _write_exchange_cache(day, validated)
    print(f"official share cache stored: {day} ({len(validated)} rows)", flush=True)
    return validated


def install_resilient_sources() -> None:
    install_ipv4_preference()
    base.fetch_sse_shares = resilient_fetch_sse_shares
    base.fetch_exchange_shares = resilient_fetch_exchange_shares
    guarded.install_guards()
    # The guarded source is a same-day realtime feed. Keep it first, but make
    # historical rebuilds recoverable after that feed's publication window.
    base.fetch_reference_prices = historical_nav_fallback.fetch_reference_prices


def main() -> int:
    install_resilient_sources()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

