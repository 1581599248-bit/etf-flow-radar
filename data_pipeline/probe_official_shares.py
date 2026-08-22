"""Lightweight readiness probe for official SSE/SZSE ETF closing shares.

This command deliberately does not build the client report.  It requests one
exact trade date, applies the same official-source validation used by the
production pipeline, and stores the verified cross-section in the shared
transport cache.  Scheduled workflows can therefore poll cheaply and only run
the expensive report build after both exchanges are ready.

Exit codes:
* 0: both exchanges are verified and cached;
* 75: retryable upstream state (not published, network, timeout, WAF);
* 2: non-retryable quality/schema failure that must block publication.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import update_daily as base
import update_daily_resilient as resilient


RETRYABLE_EXIT = 75
QUALITY_EXIT = 2


def classify_probe_error(exc: Exception) -> tuple[str, bool]:
    """Return a stable failure category and whether another poll is safe."""
    message = str(exc).lower()
    quality_markers = (
        "schema",
        "unit",
        "duplicate",
        "invalid row",
        "implausibly",
        "only ",
        "date differs",
        "unknown exchange",
    )
    if any(marker in message for marker in quality_markers):
        return "quality_gate", False
    if "403" in message or "429" in message or "waf" in message or "retry-after" in message:
        return "rate_limited", True
    if any(
        marker in message
        for marker in (
            "not been published",
            "not a complete",
            "returned no etf share rows",
            "omitted result rows",
            "no result rows",
        )
    ):
        return "not_published", True
    if any(
        marker in message
        for marker in (
            "network is unreachable",
            "newconnectionerror",
            "connection refused",
            "connection reset",
            "name resolution",
            "temporary failure",
            "timed out",
            "timeout",
            "proxyerror",
            "remote disconnected",
        )
    ):
        return "network", True
    # Unknown transport/library exceptions are retried by the next scheduled
    # probe.  The production quality gate still prevents publication.
    return "upstream_unknown", True


def probe_official_shares(
    day: date,
    fetcher: Callable[[date], pd.DataFrame],
    *,
    min_rows: int = base.MIN_MARKET_ETFS,
) -> tuple[dict[str, Any], int]:
    """Fetch and validate one exact official cross-section."""
    try:
        frame = fetcher(day)
    except Exception as exc:
        category, retryable = classify_probe_error(exc)
        return (
            {
                "schemaVersion": 1,
                "tradeDate": day.isoformat(),
                "status": "retryable" if retryable else "quality_failed",
                "category": category,
                "message": str(exc),
                "source": "official_sse_szse_eod_shares",
            },
            RETRYABLE_EXIT if retryable else QUALITY_EXIT,
        )

    required = {"code", "name", "trade_date", "shares", "exchange"}
    try:
        if frame.empty or not required.issubset(frame.columns):
            raise ValueError("official share schema is incomplete")
        checked = frame[list(required)].copy()
        checked["code"] = checked["code"].astype(str).str.zfill(6)
        checked["trade_date"] = pd.to_datetime(
            checked["trade_date"], errors="raise"
        ).dt.strftime("%Y-%m-%d")
        checked["shares"] = pd.to_numeric(checked["shares"], errors="coerce")
        if set(checked["trade_date"].unique()) != {day.isoformat()}:
            raise ValueError("official share date differs from requested trade date")
        if set(checked["exchange"].astype(str).unique()) != {"SSE", "SZSE"}:
            raise ValueError("official share response must contain both SSE and SZSE")
        if len(checked) < min_rows:
            raise ValueError(f"official share response has only {len(checked)} rows")
        if checked["code"].duplicated().any():
            raise ValueError("official share response contains duplicate ETF codes")
        if checked[["code", "name", "shares", "exchange"]].isna().any().any():
            raise ValueError("official share response contains invalid rows")
        if (checked["shares"] < 0).any():
            raise ValueError("official share response contains negative shares")
    except Exception as exc:
        return (
            {
                "schemaVersion": 1,
                "tradeDate": day.isoformat(),
                "status": "quality_failed",
                "category": "quality_gate",
                "message": str(exc),
                "source": "official_sse_szse_eod_shares",
            },
            QUALITY_EXIT,
        )

    return (
        {
            "schemaVersion": 1,
            "tradeDate": day.isoformat(),
            "status": "ready",
            "category": "verified",
            "rowCount": int(len(checked)),
            "exchangeRows": {
                str(exchange): int(count)
                for exchange, count in checked.groupby("exchange").size().items()
            },
            "source": "official_sse_szse_eod_shares",
        },
        0,
    )


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    temporary.replace(path)


def resolve_trade_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    path = base.PUBLIC / "order_flow" / "latest.json"
    payload = json.loads(path.read_text("utf-8"))
    trade_date = str(payload.get("tradeDate") or "").strip()
    if not trade_date:
        raise ValueError("latest order-flow snapshot has no tradeDate")
    return date.fromisoformat(trade_date)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Exact target trade date (YYYY-MM-DD)")
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()

    try:
        day = resolve_trade_date(args.date)
    except Exception as exc:
        payload = {
            "schemaVersion": 1,
            "status": "quality_failed",
            "category": "target_date",
            "message": str(exc),
        }
        if args.status_file:
            write_status(args.status_file, payload)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return QUALITY_EXIT

    resilient.install_resilient_sources()
    payload, exit_code = probe_official_shares(day, base.fetch_exchange_shares)
    if args.status_file:
        write_status(args.status_file, payload)
    stream = sys.stdout if exit_code == 0 else sys.stderr
    print(json.dumps(payload, ensure_ascii=False), file=stream)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
