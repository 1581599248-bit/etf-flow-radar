"""Production entrypoint for the ETF flow monitor.

This module is the final policy layer.  It keeps exchange end-of-day shares as
the source of truth, confirms split/consolidation events with exact-date NAV,
separates the A-share equity market total from classification coverage, and
builds true SW2021 level-1 rollups above hot-theme leaf groups.

Important source hierarchy:
1. SSE/SZSE end-of-day shares are authoritative and are never overwritten by a
   vendor value merely because the vendor series looks smoother.
2. Same-day average traded price is the preferred valuation convention for the
   one-day flow estimate; a price/NAV inconsistency falls back to NAV.
3. A share jump close to a rational split factor is treated as a corporate
   action only when the inverse NAV discontinuity confirms it (legacy price
   history is only a fallback confirmation source).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded
import update_daily_resilient as resilient

_HANDLED_GUARD_CHECKS = {
    "secondary_share_crosscheck",
    "price_nav_guard",
    "corporate_action_adjustment",
}
_ORIG_APPEND_ISSUE = guarded._append_issue
_LEGACY_SPLIT_CONFIRM = guarded._confirm_split_by_price
_LEGACY_GUARDED_WINDOW = guarded.guarded_fetch_share_window
_LEGACY_GUARDED_BUILD = guarded.guarded_build_snapshot

_THS_CACHE: dict[date, pd.DataFrame] = {}
_LAST_WINDOW: list[tuple[date, pd.DataFrame]] = []


def production_append_issue(
    snapshot: dict[str, Any], severity: str, check: str, message: str
) -> None:
    if severity == "warning" and check in _HANDLED_GUARD_CHECKS:
        snapshot.setdefault("quality", {}).setdefault("issues", []).append(
            {"severity": "info", "check": check, "message": message}
        )
        return
    _ORIG_APPEND_ISSUE(snapshot, severity, check, message)


def _get_ths_day(day: date) -> pd.DataFrame:
    """Exact-date independent NAV/fund-type panel, cached once per run."""
    if day in _THS_CACHE:
        return _THS_CACHE[day].copy()

    raw = base.retry(
        f"THS ETF exact-date panel {day.isoformat()}",
        lambda: base.ak.fund_etf_category_ths(symbol="ETF", date=day.strftime("%Y%m%d")),
        attempts=2,
    )
    required = {
        "基金代码", "基金名称", "当前-单位净值", "前一日-单位净值", "基金类型", "查询日期"
    }
    if raw.empty or not required.issubset(raw.columns):
        missing = sorted(required - set(raw.columns))
        raise ValueError(f"THS exact-date ETF panel schema changed; missing={missing}")

    out = raw[[
        "基金代码", "基金名称", "当前-单位净值", "前一日-单位净值", "基金类型", "查询日期"
    ]].copy()
    out.columns = ["code", "fund_name", "nav", "prev_nav", "fund_type", "query_date"]
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["nav"] = pd.to_numeric(out["nav"], errors="coerce")
    out["prev_nav"] = pd.to_numeric(out["prev_nav"], errors="coerce")
    out["query_date"] = pd.to_datetime(out["query_date"], errors="coerce").dt.date
    out = out[out["query_date"] == day].drop_duplicates("code", keep="last")
    if out.empty:
        raise ValueError(f"THS exact-date ETF panel has no rows for {day.isoformat()}")
    _THS_CACHE[day] = out
    return out.copy()


def production_audit_current_shares(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    secondary: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Cross-check vendor shares without ever replacing official exchange shares.

    A stale secondary source is useful evidence that an event deserves review,
    but continuity is not proof that the official exchange observation is wrong.
    The old implementation violated this rule and erased the 588710 1:3 split.
    """
    result = current.copy()
    scale, median_error, common_count = guarded.infer_secondary_share_scale(current, secondary)
    audit: dict[str, Any] = {
        "status": "usable" if scale is not None else "unusable",
        "scale": scale,
        "medianRelativeError": None if median_error is None else round(float(median_error), 6),
        "commonCount": common_count,
        "repaired": [],
        "disagreements": [],
        "policy": "official_exchange_shares_retained",
    }
    if scale is None:
        return result, audit

    sec = secondary[["code", "secondary_shares_raw"]].copy()
    sec["secondary_shares"] = pd.to_numeric(sec["secondary_shares_raw"], errors="coerce") * scale
    prev = previous[["code", "shares"]].rename(columns={"shares": "previous_shares"})
    joined = result[["code", "name", "shares"]].merge(prev, on="code", how="left").merge(
        sec[["code", "secondary_shares"]], on="code", how="left"
    )
    for row in joined.itertuples(index=False):
        cur = float(row.shares) if pd.notna(row.shares) else math.nan
        prv = float(row.previous_shares) if pd.notna(row.previous_shares) else math.nan
        alt = float(row.secondary_shares) if pd.notna(row.secondary_shares) else math.nan
        if not all(math.isfinite(x) and x > 0 for x in (cur, prv, alt)):
            continue
        jump = cur / prv - 1
        current_gap = alt / cur - 1
        previous_gap = alt / prv - 1
        if (
            abs(jump) >= guarded.EXTREME_SHARE_JUMP
            and abs(current_gap) >= guarded.SECONDARY_CURRENT_DISAGREEMENT
            and abs(previous_gap) <= guarded.SECONDARY_PREVIOUS_CONTINUITY
        ):
            audit["disagreements"].append({
                "code": str(row.code),
                "name": str(row.name),
                "officialShares": round(cur, 2),
                "secondaryShares": round(alt, 2),
                "previousOfficialShares": round(prv, 2),
                "officialJumpPct": round(jump * 100, 2),
                "action": "official_retained_event_check_required",
            })
    return result, audit


def production_confirm_split(
    code: str, previous_day: date, current_day: date, factor: float
) -> bool:
    """Confirm a share-unit event with inverse exact-date NAV before price history."""
    try:
        panel = _get_ths_day(current_day)
        row = panel[panel["code"] == str(code)]
        if not row.empty:
            nav = float(row.iloc[-1]["nav"])
            prev_nav = float(row.iloc[-1]["prev_nav"])
            if all(math.isfinite(x) and x > 0 for x in (nav, prev_nav, factor)):
                nav_ratio = nav / prev_nav
                expected = 1.0 / float(factor)
                if abs(nav_ratio / expected - 1) <= 0.12:
                    return True
                # An exact-date NAV pair exists and does not confirm the event.
                # Do not let a noisier trading-price source override that evidence.
                return False
    except Exception as exc:
        print(f"[warn] exact-date NAV split check failed for {code}: {exc}", file=base.sys.stderr)
    return _LEGACY_SPLIT_CONFIRM(code, previous_day, current_day, factor)


def production_fetch_share_window(
    end_day: date,
    end_frame: pd.DataFrame,
    sessions: int = base.WINDOW_SESSIONS,
) -> list[tuple[date, pd.DataFrame]]:
    global _LAST_WINDOW
    window = _LEGACY_GUARDED_WINDOW(end_day, end_frame, sessions)
    if sessions == base.WINDOW_SESSIONS:
        _LAST_WINDOW = [(d, f.copy()) for d, f in window]
    return window


def _is_a_share_equity(name: str, fund_name: str, fund_type: str) -> bool:
    if str(fund_type).strip() != "股票型":
        return False
    return not bool(base.EXCLUDE.search(f"{name} {fund_name}"))


def _rebuild_market_scope(snapshot: dict[str, Any], day: date) -> None:
    """Make the market headline independent from theme/industry classification."""
    if not _LAST_WINDOW:
        production_append_issue(
            snapshot, "warning", "market_scope_window", "未取得可复核的完整份额窗口，市场总量沿用已归类ETF口径。"
        )
        return

    try:
        ths = _get_ths_day(day)
    except Exception as exc:
        production_append_issue(
            snapshot, "warning", "market_scope_fund_type", f"独立基金类型源不可用，市场总量沿用已归类ETF口径：{exc}"
        )
        return

    universe = pd.DataFrame(snapshot.get("universe", []))
    required = {"code", "name", "shares", "referencePrice"}
    if universe.empty or not required.issubset(universe.columns):
        production_append_issue(
            snapshot, "warning", "market_scope_universe", "完整ETF名册缺少份额或估值字段，市场总量沿用已归类ETF口径。"
        )
        return

    frame = universe[["code", "name", "shares", "referencePrice"]].copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce")
    frame["referencePrice"] = pd.to_numeric(frame["referencePrice"], errors="coerce")
    frame = frame.merge(ths[["code", "fund_name", "fund_type", "nav", "prev_nav"]], on="code", how="left")
    eligible = frame.apply(
        lambda r: _is_a_share_equity(str(r["name"]), str(r.get("fund_name", "")), str(r.get("fund_type", ""))),
        axis=1,
    )
    frame = frame[eligible].copy()

    dates = [d for d, _ in _LAST_WINDOW]
    shares_by_date = {d: f.set_index("code")["shares"] for d, f in _LAST_WINDOW}
    for offset, label in ((1, "1d"), (5, "5d"), (20, "20d")):
        start = dates[-offset - 1]
        frame[f"shares_{label}"] = frame["code"].map(shares_by_date[start])
        frame[f"delta_{label}"] = frame["shares"] - frame[f"shares_{label}"]
        frame[f"flow_{label}"] = frame[f"delta_{label}"] * frame["referencePrice"] / 1e8

    one = frame.dropna(subset=["shares_1d", "referencePrice"]).copy()
    if one.empty:
        production_append_issue(
            snapshot, "warning", "market_scope_empty", "A股股票ETF主口径未形成有效样本，市场总量沿用已归类ETF口径。"
        )
        return

    five = frame.dropna(subset=["shares_5d", "referencePrice"])
    twenty = frame.dropna(subset=["shares_20d", "referencePrice"])
    count1 = {
        "increase": int((one["delta_1d"] > 0).sum()),
        "decrease": int((one["delta_1d"] < 0).sum()),
        "unchanged": int((one["delta_1d"] == 0).sum()),
    }
    top_in = one.loc[one["flow_1d"].idxmax()]
    top_out = one.loc[one["flow_1d"].idxmin()]
    aum_price = one["nav"].where(one["nav"].notna() & (one["nav"] > 0), one["referencePrice"])
    aum = float((one["shares"] * aum_price / 1e8).sum())

    market = snapshot.setdefault("market", {})
    old_market = dict(market)
    market.update({
        "name": "A股股票ETF主口径",
        "etfCount": int(len(one)),
        "etfCount5d": int(len(five)),
        "etfCount20d": int(len(twenty)),
        "flow1d": round(float(one["flow_1d"].sum()), 2),
        "flow5d": round(float(five["flow_5d"].sum()), 2),
        "flow20d": round(float(twenty["flow_20d"].sum()), 2),
        "aum": round(aum, 2),
        "breadth1d": round(float((count1["increase"] - count1["decrease"]) / len(one) * 100), 1),
        "increaseEtfCount1d": count1["increase"],
        "decreaseEtfCount1d": count1["decrease"],
        "unchangedEtfCount1d": count1["unchanged"],
        "unchangedEtfPct1d": round(count1["unchanged"] / len(one) * 100, 2),
        "topInflowEtf": {
            "code": str(top_in["code"]), "name": str(top_in["name"]),
            "flow1d": round(float(top_in["flow_1d"]), 2),
        },
        "topOutflowEtf": {
            "code": str(top_out["code"]), "name": str(top_out["name"]),
            "flow1d": round(float(top_out["flow_1d"]), 2),
        },
        "scope": "fund_type_equity_minus_crossborder_bond_commodity_money",
        "multiDayMethod": "endpoint_share_change_times_current_reference_price",
    })

    classified_flow = round(sum(float(g.get("flow1d", 0)) for g in snapshot.get("groups", [])), 2)
    quality = snapshot.setdefault("quality", {})
    quality["marketScopeEtfCount"] = int(len(one))
    quality["marketScope5dCount"] = int(len(five))
    quality["marketScope20dCount"] = int(len(twenty))
    quality["marketScopeSource"] = "同花顺精确交易日基金类型 + 沪深交易所日终份额"
    quality["classifiedCoverageOfMarketPct"] = round(len(snapshot.get("etfs", [])) / len(one) * 100, 2)
    quality["marketScopeReconciliation"] = {
        "previousClassifiedMarketFlow1d": old_market.get("flow1d"),
        "aShareEquityMarketFlow1d": market["flow1d"],
        "classifiedGroupFlow1d": classified_flow,
        "ungroupedDifference": round(float(market["flow1d"]) - classified_flow, 2),
    }


def _build_industry_rollups(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    industry_defs = {r["id"]: r for r in base.RULES.get("industry", [])}
    detail_parent = {
        r["id"]: r.get("parent") for r in base.RULES.get("industryDetail", []) if r.get("parent")
    }
    buckets: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("etfs", []):
        if item.get("kind") != "industry":
            continue
        leaf = str(item.get("groupId"))
        parent = str(detail_parent.get(leaf, leaf))
        rule = industry_defs.get(parent)
        if rule is None:
            continue
        bucket = buckets.setdefault(parent, {
            "id": parent, "code": rule.get("code"), "name": rule["name"], "kind": "industryRollup",
            "flow1d": 0.0, "flow5d": 0.0, "flow20d": 0.0, "aum": 0.0,
            "etfCount": 0, "gross1d": 0.0, "increaseEtfCount1d": 0,
            "decreaseEtfCount1d": 0, "unchangedEtfCount1d": 0,
            "dominantEtf": None, "leafGroups": set(),
        })
        f1 = float(item.get("flow1d", 0) or 0)
        f5 = float(item.get("flow5d", 0) or 0)
        f20 = float(item.get("flow20d", 0) or 0)
        bucket["flow1d"] += f1
        bucket["flow5d"] += f5
        bucket["flow20d"] += f20
        bucket["aum"] += float(item.get("aum", 0) or 0)
        bucket["etfCount"] += 1
        bucket["gross1d"] += abs(f1)
        bucket["leafGroups"].add(leaf)
        if f1 > 0:
            bucket["increaseEtfCount1d"] += 1
        elif f1 < 0:
            bucket["decreaseEtfCount1d"] += 1
        else:
            bucket["unchangedEtfCount1d"] += 1
        dominant = bucket["dominantEtf"]
        if dominant is None or abs(f1) > abs(float(dominant["flow1d"])):
            bucket["dominantEtf"] = {
                "code": str(item.get("code")), "name": str(item.get("name")), "flow1d": round(f1, 2)
            }

    result: list[dict[str, Any]] = []
    for bucket in buckets.values():
        gross = float(bucket.pop("gross1d"))
        leaf_groups = sorted(bucket.pop("leafGroups"))
        bucket["flow1d"] = round(float(bucket["flow1d"]), 2)
        bucket["flow5d"] = round(float(bucket["flow5d"]), 2)
        bucket["flow20d"] = round(float(bucket["flow20d"]), 2)
        bucket["aum"] = round(float(bucket["aum"]), 2)
        bucket["concentration1d"] = (
            round(abs(float(bucket["dominantEtf"]["flow1d"])) / gross * 100, 1) if gross else 0.0
        )
        bucket["leafGroups"] = leaf_groups
        result.append(bucket)
    return sorted(result, key=lambda x: -float(x["flow1d"]))


def _regenerate_conclusion(snapshot: dict[str, Any]) -> None:
    market = snapshot["market"]
    groups = snapshot.get("groups", [])
    broad = [g for g in groups if g.get("kind") == "broad"]
    styles = [g for g in groups if g.get("kind") == "style"]
    sectors = snapshot.get("industryRollups", [])
    if not broad or not sectors:
        return

    broad_in_count = sum(float(g.get("flow1d", 0)) > 0 for g in broad)
    broad_out_count = sum(float(g.get("flow1d", 0)) < 0 for g in broad)
    sec_in = sorted(sectors, key=lambda g: float(g.get("flow1d", 0)), reverse=True)
    sec_out = sorted(sectors, key=lambda g: float(g.get("flow1d", 0)))
    direction = base._direction(float(market["flow1d"]))
    sector_headline = (
        f"申万一级行业资金流入居前的是{sec_in[0]['name']}，流出最多的是{sec_out[0]['name']}。"
        if float(sec_in[0]["flow1d"]) > 0
        else f"申万一级行业当日均未录得净流入，流出最多的是{sec_out[0]['name']}。"
    )
    headline = (
        f"本期统计的{market['etfCount']}只A股股票ETF当日合计{direction}{abs(float(market['flow1d'])):.1f}亿元；"
        f"净流入{market['increaseEtfCount1d']}只、净流出{market['decreaseEtfCount1d']}只。"
        f"宽基中{broad_out_count}个流出、{broad_in_count}个流入；{sector_headline}"
    )

    broad_out = sorted(broad, key=lambda g: float(g.get("flow1d", 0)))
    broad_line = "宽基流出前三为" + "、".join(
        f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in broad_out[:3]
    ) + f"；5日端点变化流出最大为{min(broad,key=lambda g:float(g.get('flow5d',0)))['name']}。"
    positive = [g for g in sec_in if float(g.get("flow1d", 0)) > 0]
    if positive:
        sector_line = "申万一级行业净流入居前为" + "、".join(
            f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in positive[:2]
        ) + f"；净流出最多为{sec_out[0]['name']}{float(sec_out[0]['flow1d']):+.1f}亿。"
    else:
        sector_line = f"申万一级行业当日均未录得净流入；流出最多为{sec_out[0]['name']}{float(sec_out[0]['flow1d']):+.1f}亿。"
    anomaly = (
        f"单只ETF大额变化：{market['topInflowEtf']['name']}净流入{float(market['topInflowEtf']['flow1d']):+.1f}亿元；"
        f"{market['topOutflowEtf']['name']}净流出{float(market['topOutflowEtf']['flow1d']):+.1f}亿元。"
    )

    sustained = sorted(
        [g for g in groups if float(g.get("flow1d", 0)) > 0 and float(g.get("flow5d", 0)) > 0],
        key=lambda g: float(g.get("flow5d", 0)), reverse=True,
    )
    sustained_text = (
        f"{sustained[0]['name']}的1日与5日端点份额变化均为净流入，可继续观察延续性。"
        if sustained else "目前没有观察组同时满足1日与5日端点份额变化净流入。"
    )
    if styles:
        style_in = max(styles, key=lambda g: float(g.get("flow1d", 0)))
        style_out = min(styles, key=lambda g: float(g.get("flow1d", 0)))
        interpretation = (
            f"从份额数据看，宽基当日{broad_out_count}/{len(broad)}个组净流出；"
            f"风格组中{style_in['name']}当日变化相对靠前，{style_out['name']}流出较多。{sustained_text}"
        )
    else:
        interpretation = f"从份额数据看，宽基当日{broad_out_count}/{len(broad)}个组净流出。{sustained_text}"

    snapshot["conclusion"].update({
        "headline": headline,
        "facts": [broad_line, sector_line, anomaly],
        "interpretation": interpretation,
    })


def _postprocess_snapshot(snapshot: dict[str, Any], day: date) -> None:
    _rebuild_market_scope(snapshot, day)
    rollups = _build_industry_rollups(snapshot)
    snapshot["industryRollups"] = rollups
    snapshot["themeGroups"] = [
        g for g in snapshot.get("groups", []) if g.get("kind") == "industry" and g.get("parent")
    ]
    snapshot.setdefault("quality", {})["industryRollupCount"] = len(rollups)
    snapshot["quality"]["themeGroupCount"] = len(snapshot["themeGroups"])

    share_audit = snapshot.get("quality", {}).get("shareCrossCheck", {})
    disagreements = share_audit.get("disagreements", []) if isinstance(share_audit, dict) else []
    if disagreements:
        snapshot.setdefault("quality", {}).setdefault("issues", []).append({
            "severity": "info",
            "check": "secondary_share_disagreement",
            "message": f"{len(disagreements)}只ETF的第三方最新份额与交易所官方份额显著不一致；官方份额已保留，并进入公司行动校验。",
        })

    snapshot.setdefault("methodology", {}).update({
        "flow": "1日参考净申赎 =（T日交易所日终份额 − T-1日经公司行动调整后的可比份额）× T日成交均价；成交均价与同日NAV异常偏离时回退NAV。交易所日终份额为主源，第三方份额只做交叉核验，不覆盖官方值。",
        "multiDay": "5日/20日当前展示端点份额变化估算：期末份额减去5/20个交易日前可比份额，再乘期末参考价；它用于观察中期份额方向，不等同逐日资金流之和，因此页面不再称为‘累计净流入’。",
        "scope": "市场总量先按精确交易日基金类型筛选股票型ETF，再剔除跨境、债券、商品、货币等非A股股票品种；市场总量不依赖行业/主题分类是否完成。分组统计仅使用已归类产品。",
        "classification": "申万一级行业与热门主题采用两层结构：ETF仍保留唯一叶子归属避免重复计数；申万一级行业榜单使用父级汇总，自动把半导体/芯片/消费电子等子主题回卷到电子等对应一级行业，热门主题单独展示，不再把子主题称为申万一级行业。",
        "aum": "页面规模为参考规模；市场总规模优先按同日NAV×份额估算，分组参考规模沿用当前参考价口径并明确标注为参考值。",
    })
    snapshot["methodology"]["coordinates"] = "横轴 = 20日相对沪深300收益率；纵轴 = 5日端点份额变化估算 ÷ 5日前参考规模（%）。"
    _regenerate_conclusion(snapshot)


def production_build_snapshot(day: date, current: pd.DataFrame | None = None) -> dict[str, Any]:
    snapshot = _LEGACY_GUARDED_BUILD(day, current)
    _postprocess_snapshot(snapshot, day)
    return snapshot


def install_production_pipeline() -> None:
    _THS_CACHE.clear()
    global _LAST_WINDOW
    _LAST_WINDOW = []
    guarded._append_issue = production_append_issue
    guarded.repair_current_shares = production_audit_current_shares
    guarded._confirm_split_by_price = production_confirm_split
    guarded.guarded_fetch_share_window = production_fetch_share_window
    resilient.install_resilient_sources()
    # resilient.install_resilient_sources() installs guarded.guarded_build_snapshot;
    # replace only the final build callable with our post-processing orchestrator.
    base.build_snapshot = production_build_snapshot


def main() -> int:
    install_production_pipeline()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
