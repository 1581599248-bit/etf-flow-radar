from __future__ import annotations

import json
import math
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

import update_daily as core

HERE = Path(__file__).resolve().parent
V2 = json.loads((HERE / "classification_v2.json").read_text("utf-8"))
BASE = json.loads((HERE / "classification.json").read_text("utf-8"))
TAXONOMY_VERSION = V2["taxonomyVersion"]

# One code-level provider adapter: all network calls go through AKShare.
# Shares remain official exchange data; metadata/NAV/return series use Eastmoney via AKShare.
PROVIDER = "AKSHARE_UNIFIED"

RULES = {
    "broad": BASE["broad"],
    "style": BASE["style"],
    "theme": V2["theme"],
    "industry": BASE["industry"],
}
PRIORITY = V2["priority"]


def _combined(name: str, price_name: str | None) -> str:
    return " ".join(v for v in (name, price_name) if v)


def classify_etf_v2(name: str, price_name: str | None = None) -> dict[str, Any] | None:
    text = _combined(name, price_name)
    if core.EXCLUDE.search(text):
        return None
    for kind in PRIORITY:
        best: tuple[tuple[int, int], dict[str, Any]] | None = None
        for rule in RULES[kind]:
            for pattern in rule["patterns"]:
                match = re.search(pattern, text, re.IGNORECASE)
                if not match:
                    continue
                score = (match.start(), -(match.end() - match.start()))
                if best is None or score < best[0]:
                    best = (score, rule)
        if best:
            return {"kind": kind, **best[1]}
    return None


def fetch_sse_shares_v2(day: date) -> pd.DataFrame:
    """Use AKShare's official SSE ETF share adapter instead of a hand-written HTTP call."""
    stamp = day.strftime("%Y%m%d")
    frame = core.retry("SSE ETF shares via AKShare", lambda: ak.fund_etf_scale_sse(date=stamp))
    if frame.empty:
        return frame
    required = {"基金代码", "基金简称", "统计日期", "基金份额"}
    if not required.issubset(frame.columns):
        raise ValueError("AKShare SSE ETF share schema changed")
    frame = frame.copy()
    # AKShare preserves SSE's ten-thousand-share unit for this endpoint.
    frame["基金份额"] = pd.to_numeric(frame["基金份额"], errors="coerce") * 10000
    return frame


def fetch_return_series_v2(representatives: list[dict[str, str]], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Use Eastmoney historical ETF NAV through AKShare for group return proxies.

    This removes the previous Sina market-data dependency. The same Eastmoney
    NAV family is therefore used for both flow valuation and return proxies.
    """
    output: dict[str, pd.DataFrame] = {}
    attempted_groups: set[str] = set()
    total_groups = len({r["group_id"] for r in representatives})
    done = 0
    for rep in representatives:
        group_id = rep["group_id"]
        if group_id in output:
            continue
        first = group_id not in attempted_groups
        attempted_groups.add(group_id)
        try:
            raw = core.retry(
                f"Eastmoney ETF NAV history {rep['code']}",
                lambda code=rep["code"]: ak.fund_etf_fund_info_em(
                    fund=code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                ),
                attempts=2,
            )
            if raw.empty or not {"净值日期", "单位净值"}.issubset(raw.columns):
                raise ValueError("ETF NAV history unavailable")
            frame = raw[["净值日期", "单位净值"]].copy()
            frame.columns = ["date", "close"]
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            frame = frame.dropna().sort_values("date")
            if len(frame) < core.WINDOW_SESSIONS:
                raise ValueError("insufficient ETF NAV history")
            if frame["close"].pct_change().abs().max() > .25:
                raise ValueError("NAV discontinuity")
            frame.attrs["code"] = rep["code"]
            frame.attrs["name"] = rep["name"]
            output[group_id] = frame
            done += 1
        except Exception as exc:
            print(f"NAV history warning {rep['code']}: {exc}", file=sys.stderr)
        if first or group_id in output:
            print(f"return proxy history: {done}/{total_groups} groups", flush=True)
        time.sleep(.05)
    return output


def _display_name(row: dict[str, Any]) -> str:
    full = str(row.get("fullName") or "").strip()
    short = str(row.get("name") or "").strip()
    return full or short


def _group_rank(groups: list[dict[str, Any]], kinds: tuple[str, ...], key: str, reverse: bool, limit: int = 3) -> list[dict[str, Any]]:
    rows = [g for g in groups if g.get("kind") in kinds]
    return sorted(rows, key=lambda g: g.get(key) or 0, reverse=reverse)[:limit]


def enrich_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot["schemaVersion"] = 8
    snapshot["taxonomyVersion"] = TAXONOMY_VERSION
    snapshot["provider"] = {
        "id": PROVIDER,
        "description": "AKShare统一数据适配层：沪深ETF份额取交易所官方数据；基金类型、净值与收益代理统一取东方财富基金数据。",
    }

    by_code: dict[str, dict[str, Any]] = {str(r["code"]): r for r in snapshot.get("etfs", [])}
    for row in snapshot.get("etfs", []):
        row["displayName"] = _display_name(row)
    for row in snapshot.get("universe", []):
        row["displayName"] = _display_name(row)

    for key in ("topInflowEtf", "topOutflowEtf"):
        item = snapshot["market"].get(key)
        if not item:
            continue
        source = by_code.get(str(item["code"]), {})
        item["name"] = _display_name(source) or item["name"]
        item["displayName"] = item["name"]

    for group in snapshot.get("groups", []):
        dom = group.get("dominantEtf")
        if dom:
            source = by_code.get(str(dom["code"]), {})
            dom["name"] = _display_name(source) or dom["name"]
            dom["displayName"] = dom["name"]
        rep = group.get("representative")
        if rep:
            source = by_code.get(str(rep["code"]), {})
            rep["name"] = _display_name(source) or rep["name"]

    # The old focus/unclassified presentation is intentionally retired.
    snapshot.pop("focusEtfs", None)

    groups = snapshot.get("groups", [])
    market = snapshot["market"]
    broad = [g for g in groups if g.get("kind") == "broad"]
    broad_out = sorted(broad, key=lambda g: g.get("flow1d", 0))[:3]
    broad_in = sorted(broad, key=lambda g: g.get("flow1d", 0), reverse=True)[:3]
    theme_in = _group_rank(groups, ("theme", "industry", "style"), "flow1d", True, 3)
    theme_out = _group_rank(groups, ("theme", "industry", "style"), "flow1d", False, 3)

    def fmt_group(rows: list[dict[str, Any]]) -> str:
        return "、".join(f"{g['name']}{g['flow1d']:+.1f}亿" for g in rows)

    top_in = market["topInflowEtf"]
    top_out = market["topOutflowEtf"]
    broad_out_count = sum((g.get("flow1d") or 0) < 0 for g in broad)
    broad_in_count = sum((g.get("flow1d") or 0) > 0 for g in broad)
    market_word = "净流入" if market["flow1d"] > .05 else "净流出" if market["flow1d"] < -.05 else "基本持平"

    snapshot["conclusion"] = {
        "headline": (
            f"本期完整统计{market['etfCount']}只A股股票ETF，当日合计{market_word}{abs(market['flow1d']):.1f}亿元；"
            f"净流入{market['increaseEtfCount1d']}只、净流出{market['decreaseEtfCount1d']}只。"
            f"宽基中{broad_out_count}个流出、{broad_in_count}个流入。"
        ),
        "facts": [
            f"宽基净流出居前：{fmt_group(broad_out)}；净流入居前：{fmt_group([g for g in broad_in if g['flow1d'] > 0]) or '暂无明显净流入'}。",
            f"市场主题/行业净流入居前：{fmt_group([g for g in theme_in if g['flow1d'] > 0]) or '暂无明显净流入'}；净流出居前：{fmt_group(theme_out)}。",
            f"单只ETF大额变化：{top_in['name']}（{top_in['code']}）{top_in['flow1d']:+.1f}亿元；{top_out['name']}（{top_out['code']}）{top_out['flow1d']:+.1f}亿元。",
        ],
        "interpretation": "分类采用宽基、风格、市场主题与申万一级行业四层互斥体系；机器人、新能源、白酒、消费、AI算力等主流主题不再被强行塞入单一申万一级行业。",
        "confidence": snapshot.get("conclusion", {}).get("confidence", "A"),
        "confidenceNote": snapshot.get("conclusion", {}).get("confidenceNote", ""),
    }

    snapshot["methodology"]["return"] = "组别收益使用组内当前规模较大的ETF单位净值作为收益代理；与资金估值统一使用东方财富基金净值数据。"
    snapshot["methodology"]["classification"] = (
        f"{TAXONOMY_VERSION}：宽基、风格、市场主题、申万一级行业四层互斥分类。"
        "优先识别机器人、新能源、新能源车、电池、光伏、储能、白酒、消费、AI算力、半导体、创新药、央国企等主流市场主题；"
        "只有未命中更具体主题的产品才进入申万一级行业。"
    )
    snapshot["sources"] = [
        {"name":"AKShare / 上海证券交易所","field":"沪市ETF日终总份额","role":"官方主数据"},
        {"name":"AKShare / 深圳证券交易所","field":"深市ETF日终总份额","role":"官方主数据"},
        {"name":"AKShare / 东方财富基金","field":"基金类型、当日单位净值、历史单位净值","role":"统一基金元数据、资金估值与收益代理"},
    ]
    snapshot["quality"]["taxonomyVersion"] = TAXONOMY_VERSION
    snapshot["quality"]["provider"] = PROVIDER
    snapshot["quality"]["themeGroupCount"] = sum(g.get("kind") == "theme" for g in groups)
    snapshot["quality"]["visibleUngroupedModule"] = False
    return snapshot


def configure_core() -> None:
    core.RULES = RULES
    core.FOCUS_RULES = []
    core.classify_etf = classify_etf_v2
    core.fetch_sse_shares = fetch_sse_shares_v2
    core.fetch_return_series = fetch_return_series_v2


def build_snapshot_v2(day: date, current: pd.DataFrame | None = None) -> dict[str, Any]:
    configure_core()
    snapshot = core.build_snapshot(day, current)
    return enrich_snapshot(snapshot)


def main() -> int:
    import argparse

    configure_core()
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to latest complete official session")
    args = parser.parse_args()
    try:
        if args.date:
            day = date.fromisoformat(args.date)
            current = core.fetch_exchange_shares(day)
        else:
            day, current = core.fetch_available_shares(core.latest_weekday(date.today()))
            existing = core.PUBLIC / "latest.json"
            if existing.exists():
                old = json.loads(existing.read_text("utf-8"))
                if old.get("tradeDate") == day.isoformat() and old.get("schemaVersion", 0) >= 8 and old.get("taxonomyVersion") == TAXONOMY_VERSION:
                    print(f"no new complete official session: {day.isoformat()}")
                    return 0
        snapshot = build_snapshot_v2(day, current)
        path = core.atomic_publish(snapshot)
    except Exception as exc:
        print(f"UPDATE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"verified v2 snapshot: {path} ({snapshot['tradeDate']}, {len(snapshot['etfs'])} analyzed ETFs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
