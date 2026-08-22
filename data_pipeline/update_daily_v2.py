"""Single production entrypoint for ETF Flow Radar schema v6.

Transport and guard layers remain in the older modules. This file is the single
public production entrypoint and the single client-facing schema layer. Archived
rebuilds can call ``apply_v2_semantics`` with validated local share data, so a
methodology-only JSON migration does not need to redownload historical exchange
files.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded
import update_daily_production as production
import flow_model_v2
import flow_comparison_v2
import flow_scope_breakdown_v2

_ORIG_POSTPROCESS = production._postprocess_snapshot
_ORIG_ATOMIC_PUBLISH = base.atomic_publish


def _load_secondary_spot(day: date) -> pd.DataFrame:
    path = base.PUBLIC / "order_flow" / f"{day.isoformat()}.json"
    if path.exists():
        payload = json.loads(path.read_text("utf-8"))
        if (
            payload.get("tradeDate") == day.isoformat()
            and payload.get("metric") in {"secondaryMarketMainOrderFlow", "secondaryMarketETFTradingFlow"}
        ):
            rows = payload.get("etfs", [])
            if rows:
                frame = pd.DataFrame(rows)
                frame["代码"] = frame["code"].astype(str).str.zfill(6)
                frame["名称"] = frame["name"].astype(str)
                frame["数据日期"] = day.isoformat()
                if "mainOrderFlow1d" in frame.columns:
                    frame["主力净流入-净额"] = pd.to_numeric(frame["mainOrderFlow1d"], errors="coerce") * 1e8
                if "tradeNetFlow1d" in frame.columns:
                    frame["当日交易净额"] = pd.to_numeric(frame["tradeNetFlow1d"], errors="coerce") * 1e8
                if "tradeInflow1d" in frame.columns:
                    frame["当日交易流入"] = pd.to_numeric(frame["tradeInflow1d"], errors="coerce") * 1e8
                if "tradeOutflow1d" in frame.columns:
                    frame["当日交易流出"] = pd.to_numeric(frame["tradeOutflow1d"], errors="coerce") * 1e8
                if "amount" in frame.columns:
                    frame["成交额"] = pd.to_numeric(frame["amount"], errors="coerce") * 1e8
                keep = [
                    c for c in [
                        "代码", "名称", "主力净流入-净额", "当日交易净额",
                        "当日交易流入", "当日交易流出", "成交额", "数据日期",
                    ] if c in frame.columns
                ]
                return frame[keep]
    return guarded._get_spot()


def _same_day_trade_rows(spot: pd.DataFrame | None, day: date) -> pd.DataFrame:
    """Return same-day per-ETF all-trade net flow in yuan."""
    columns = ["code", "trade_net_yuan", "trade_in_yuan", "trade_out_yuan"]
    if spot is None or spot.empty:
        return pd.DataFrame(columns=columns)
    source = spot.copy()
    source.columns = [str(c).strip() for c in source.columns]
    if not {"代码", "数据日期"}.issubset(source.columns):
        return pd.DataFrame(columns=columns)
    source["code"] = source["代码"].astype(str).str.zfill(6)
    source["data_date"] = pd.to_datetime(source["数据日期"], errors="coerce").dt.date
    source = source[source["data_date"] == day].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)
    if "当日交易净额" in source.columns:
        source["trade_net_yuan"] = pd.to_numeric(source["当日交易净额"], errors="coerce")
        source["trade_in_yuan"] = pd.to_numeric(source.get("当日交易流入"), errors="coerce")
        source["trade_out_yuan"] = pd.to_numeric(source.get("当日交易流出"), errors="coerce")
    elif {"成交额", "外盘", "内盘"}.issubset(source.columns):
        amount = pd.to_numeric(source["成交额"], errors="coerce")
        outer = pd.to_numeric(source["外盘"], errors="coerce")
        inner = pd.to_numeric(source["内盘"], errors="coerce")
        directional = outer + inner
        valid = directional > 0
        source["trade_in_yuan"] = amount * outer / directional.where(valid)
        source["trade_out_yuan"] = amount * inner / directional.where(valid)
        source["trade_net_yuan"] = source["trade_in_yuan"] - source["trade_out_yuan"]
    else:
        return pd.DataFrame(columns=columns)
    return source[columns].dropna(subset=["trade_net_yuan"]).drop_duplicates("code", keep="last")


def _add_trade_net_flow(snapshot: dict[str, Any], day: date, ths: pd.DataFrame, spot: pd.DataFrame | None) -> None:
    """Add a separate secondary-market trading statistic without changing primary flow."""
    target = {
        "metric": "secondaryMarketTradeNetFlowEstimate",
        "displayName": "当日成交资金净流入/净流出",
        "definition": "按同日ETF成交额与外盘/内盘主动成交方向估算交易资金净额；只显示主动买入金额减主动卖出金额的差额。",
        "source": "东方财富ETF同日行情 成交额 + 外盘/内盘",
        "tradeDate": day.isoformat(),
        "status": "unavailable",
        "scopeTotals": {},
    }
    exact = _same_day_trade_rows(spot, day)
    if exact.empty:
        target["reason"] = "no same-day all-trade direction snapshot"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return
    universe = pd.DataFrame(snapshot.get("universe", []))
    if universe.empty or not {"code", "name"}.issubset(universe.columns):
        target["reason"] = "snapshot universe missing code/name"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return
    frame = universe[["code", "name"]].copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame.drop_duplicates("code", keep="last").merge(ths[["code", "fund_name", "fund_type"]], on="code", how="left")
    frame["scope"] = frame.apply(lambda row: flow_model_v2._asset_scope(str(row["name"]), str(row.get("fund_name", "")), str(row.get("fund_type", ""))), axis=1)
    joined = frame[["code", "scope"]].merge(exact, on="code", how="inner")
    if joined.empty:
        target["reason"] = "same-day trading rows do not overlap the ETF universe"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return

    def total(part: pd.DataFrame) -> dict[str, Any]:
        inflow = pd.to_numeric(part["trade_in_yuan"], errors="coerce")
        outflow = pd.to_numeric(part["trade_out_yuan"], errors="coerce")
        return {
            "etfCount": int(len(part)),
            "netFlow1d": round(float(part["trade_net_yuan"].sum()) / 1e8, 2),
            "inflow1d": round(float(inflow.sum()) / 1e8, 2) if inflow.notna().any() else None,
            "outflow1d": round(float(outflow.sum()) / 1e8, 2) if outflow.notna().any() else None,
        }
    target.update({"status": "available", "scopeTotals": {
        "allEtf": total(joined),
        "stockEtfIncludingCrossBorder": total(joined[joined["scope"].isin(["aShareStockEtf", "crossBorderStockEtf"])]),
        "aShareStockEtf": total(joined[joined["scope"].eq("aShareStockEtf")]),
    }})
    snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
    trade_map = dict(zip(joined["code"], joined["trade_net_yuan"] / 1e8))
    for item in snapshot.get("etfs", []):
        value = trade_map.get(str(item.get("code", "")).zfill(6))
        item["secondaryTradeNetFlow1d"] = round(float(value), 4) if pd.notna(value) else None


def _flow_phrase(value: float) -> str:
    if value > 0:
        return f"净流入{value:.1f}亿元"
    if value < 0:
        return f"净流出{abs(value):.1f}亿元"
    return "净额0.0亿元"


def _relative_intensity(value: float, base_value: float | None) -> float | None:
    if not isinstance(base_value, (int, float)) or not pd.notna(base_value) or float(base_value) <= 0:
        return None
    return abs(float(value)) / float(base_value) * 100.0


def _trade_strength(trade_value: float, turnover: float | None) -> str:
    """Classify intraday net trading flow by its share of gross ETF turnover."""
    intensity = _relative_intensity(trade_value, turnover)
    if intensity is None:
        return "balanced" if trade_value == 0 else "generic"
    if intensity < 1.0:
        return "balanced"
    if intensity < 3.0:
        return "small"
    if intensity < 6.0:
        return "clear"
    if intensity < 10.0:
        return "large"
    return "extreme"


def _primary_strength(primary_value: float, aum: float | None) -> str:
    """Classify share subscription/redemption flow by its share of ETF AUM."""
    intensity = _relative_intensity(primary_value, aum)
    if primary_value == 0:
        return "flat"
    if intensity is None:
        return "generic"
    if intensity < 0.05:
        return "flat"
    if intensity < 0.20:
        return "small"
    if intensity < 0.50:
        return "clear"
    if intensity < 1.00:
        return "large"
    return "extreme"


# Quantified market states plus explicit missing-scale/data fallbacks.
PRIMARY_SCENARIO_COUNT = 11
TRADE_SCENARIO_COUNT = 12
ALLOCATION_SCENARIO_COUNT = 13
CONCLUSION_SCENARIO_COUNT = PRIMARY_SCENARIO_COUNT * TRADE_SCENARIO_COUNT * ALLOCATION_SCENARIO_COUNT


def _trade_copy(trade_value: float, strength: str) -> str:
    if strength == "balanced":
        return "A股ETF盘中买卖力量基本均衡"
    if strength == "generic":
        if trade_value > 0:
            return f"A股ETF盘中主动买入净额{trade_value:.1f}亿元"
        return f"A股ETF盘中主动卖出净额{abs(trade_value):.1f}亿元"
    if trade_value > 0:
        label = {
            "small": "买盘小幅偏强",
            "clear": "买盘偏强",
            "large": "买盘明显占优",
            "extreme": "买盘显著占优",
        }[strength]
        return f"A股ETF盘中{label}，主动买入净额{trade_value:.1f}亿元"
    label = {
        "small": "卖盘小幅偏强",
        "clear": "卖盘偏强",
        "large": "卖盘明显占优",
        "extreme": "卖压显著增强",
    }[strength]
    return f"A股ETF盘中{label}，主动卖出净额{abs(trade_value):.1f}亿元"


def _primary_copy(primary_value: float, strength: str) -> str:
    if strength == "flat":
        return "ETF份额对应申赎资金基本持平"
    qualifier = {
        "small": "小幅",
        "clear": "明显",
        "large": "大幅",
        "extreme": "巨量",
    }.get(strength, "")
    if primary_value > 0:
        return f"ETF份额对应申赎资金{qualifier}净流入{primary_value:.1f}亿元"
    return f"ETF份额对应申赎资金{qualifier}净流出{abs(primary_value):.1f}亿元"


def _motion_copy(primary_value: float, strength: str) -> str:
    if strength == "flat":
        return "整体资金暂无明显增减"
    if primary_value > 0:
        return {
            "small": "整体资金小幅净流入",
            "clear": "整体资金呈较明显净流入",
            "large": "整体资金净流入幅度较大",
            "extreme": "整体资金净流入幅度显著",
        }.get(strength, "整体资金呈净流入")
    return {
        "small": "整体资金小幅净流出",
        "clear": "整体资金呈较明显净流出",
        "large": "整体资金净流出幅度较大",
        "extreme": "整体资金净流出幅度显著",
    }.get(strength, "整体资金呈净流出")


def _strength_rank(strength: str | None) -> int:
    return {
        "flat": 0,
        "balanced": 0,
        "small": 1,
        "clear": 2,
        "large": 3,
        "extreme": 4,
    }.get(strength or "", 0)


def _primary_signal_copy(primary_value: float, strength: str) -> str:
    if strength == "flat":
        return "份额申赎基本平衡"
    action = "申购" if primary_value > 0 else "赎回"
    if strength == "small":
        return f"份额少量净{action}"
    if strength == "clear":
        return f"份额净{action}偏多"
    if strength == "large":
        return f"份额大量净{action}"
    if strength == "extreme":
        return f"份额巨量净{action}"
    return f"份额净{action}"


def _trade_signal_copy(trade_value: float, strength: str | None) -> str:
    if strength == "balanced":
        return "盘中买卖基本均衡"
    side = "盘中买盘" if trade_value > 0 else "盘中卖压"
    if strength == "small":
        return side + "小幅偏强"
    if strength == "clear":
        return side + "偏强"
    if strength == "large":
        return side + "明显占优"
    if strength == "extreme":
        return side + "显著占优"
    return side + "存在方向"


def _relation_copy(
    trade_value: float | None,
    primary_value: float,
    trade_strength: str | None,
    primary_strength: str,
) -> str:
    primary_signal = _primary_signal_copy(primary_value, primary_strength)
    if trade_value is None:
        return f"{primary_signal}，盘中数据暂缺"
    if trade_strength == "balanced":
        if primary_strength == "flat":
            return "份额申赎与盘中买卖均基本平衡"
        return f"{primary_signal}，盘中买卖基本均衡"
    if primary_strength == "flat":
        return f"{primary_signal}，{_trade_signal_copy(trade_value, trade_strength)}"

    trade_side = "盘中买盘" if trade_value > 0 else "盘中卖压"
    same_direction = (trade_value > 0) == (primary_value > 0)
    relation = "同步" if same_direction else "背离"
    if "generic" in {trade_strength, primary_strength}:
        return f"{primary_signal}，{trade_side}{relation}"

    trade_rank = _strength_rank(trade_strength)
    primary_rank = _strength_rank(primary_strength)
    if primary_rank > trade_rank:
        relation += "但相对有限"
    elif trade_rank > primary_rank:
        relation += "且更强"
    return f"{primary_signal}，{trade_side}{relation}"


def _base_intent_copy(primary_value: float, primary_strength: str) -> str:
    if primary_strength == "flat":
        return "整体资金方向不明"
    if primary_value > 0:
        return {
            "small": "整体市场略偏积极",
            "clear": "整体市场偏积极",
            "large": "整体市场资金意愿较强",
            "extreme": "整体市场资金意愿明显增强",
            "generic": "整体资金偏申购",
        }[primary_strength]
    return {
        "small": "整体市场略偏谨慎",
        "clear": "整体市场偏谨慎",
        "large": "整体市场谨慎情绪较强",
        "extreme": "整体市场谨慎情绪明显升温",
        "generic": "整体资金偏赎回",
    }[primary_strength]


def _overall_intent_copy(
    trade_value: float | None,
    primary_value: float,
    trade_strength: str | None,
    primary_strength: str,
) -> str:
    base_intent = _base_intent_copy(primary_value, primary_strength)
    if primary_strength == "flat":
        if trade_value is None or trade_strength == "balanced":
            return base_intent
        return "短线买盘占优，份额端尚未确认" if trade_value > 0 else "短线卖压占优，份额端尚未确认"
    if trade_value is None or trade_strength == "balanced":
        return base_intent

    same_direction = (trade_value > 0) == (primary_value > 0)
    if same_direction:
        return base_intent
    if "generic" in {trade_strength, primary_strength}:
        if primary_value > 0:
            return "申购承接存在，但资金信号分化"
        return "盘中承接存在，但赎回信号仍在"

    trade_rank = _strength_rank(trade_strength)
    primary_rank = _strength_rank(primary_strength)
    if primary_value > 0:
        if primary_rank > trade_rank:
            if primary_rank >= _strength_rank("large"):
                return "大资金逢跌进场，份额端承接有力"
            return "资金逢跌申购，份额端承接占优"
        if primary_rank == trade_rank:
            return "资金逢跌承接，但整体信号分化"
        return "盘中抛压占优，份额端承接有限"

    if primary_rank > trade_rank:
        return "赎回意愿占主导，整体市场偏谨慎"
    if primary_rank == trade_rank:
        return "资金信号分化，整体市场方向不明"
    return "盘中承接占优，但份额端尚未确认"


_GROWTH_FLOW_KEYWORDS = (
    "成长", "科技", "半导体", "创新药", "AI", "算力", "芯片", "通信", "机器人",
    "软件", "信创", "消费电子", "新能源", "光伏", "锂电", "储能", "医疗器械",
    "智能驾驶", "电子", "计算机", "传媒", "游戏", "国防军工", "卫星", "互联网",
)
_DEFENSIVE_FLOW_KEYWORDS = (
    "红利", "低波", "价值", "质量", "自由现金流", "央国企", "银行", "公用事业",
    "煤炭", "石油石化", "食品饮料", "白酒", "家用电器", "交通运输", "黄金",
)
_CYCLICAL_FLOW_KEYWORDS = (
    "有色金属", "稀土", "基础化工", "钢铁", "建筑材料", "建筑装饰", "房地产",
    "券商", "证券", "金融科技", "机械设备", "汽车", "农林牧渔", "养殖",
)


def _allocation_tilt(name: str) -> str:
    """Map a style/industry label to a conservative allocation tilt."""
    if any(keyword in name for keyword in _GROWTH_FLOW_KEYWORDS):
        return "growth"
    if any(keyword in name for keyword in _DEFENSIVE_FLOW_KEYWORDS):
        return "defensive"
    if any(keyword in name for keyword in _CYCLICAL_FLOW_KEYWORDS):
        return "cyclical"
    return "neutral"


def _inflow_focus_context(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    """Rank style and industry/theme groups together and describe the inflow focus."""
    candidates = [
        g for g in snapshot.get("groups", [])
        if g.get("kind") in {"style", "industry"}
        and isinstance(g.get("flow1d"), (int, float))
        and math.isfinite(float(g["flow1d"]))
        and str(g.get("name", "")).strip()
    ]
    if not candidates:
        return "unavailable", "资金流向暂不明确。", "unknown"

    positive = sorted(
        (
            {"name": str(g["name"]).strip(), "flow": float(g["flow1d"])}
            for g in candidates
            if float(g["flow1d"]) > 0.05
        ),
        key=lambda item: item["flow"],
        reverse=True,
    )
    if not positive:
        return "no_inflow", "资金未见明显集中流入。", "unknown"

    positive_total = sum(item["flow"] for item in positive)
    if positive_total < 0.50:
        return "limited", "资金流入有限。", "unknown"

    if len(positive) == 1 or positive[0]["flow"] / positive_total >= 0.70:
        selected = positive[:1]
    elif (
        len(positive) == 2
        or sum(item["flow"] for item in positive[:2]) / positive_total >= 0.60
        or positive[0]["flow"] >= positive[2]["flow"] * 2
    ):
        selected = positive[:2]
    else:
        return "dispersed", "资金流入较为分散。", "mixed"

    tilts = {_allocation_tilt(item["name"]) for item in selected}
    tilt = next(iter(tilts)) if len(tilts) == 1 else "mixed"
    count_label = "one" if len(selected) == 1 else "two"
    state = f"concentrated_{count_label}_{tilt}"
    names = selected[0]["name"] if len(selected) == 1 else f"{selected[0]['name']}与{selected[1]['name']}"
    return state, f"资金流入集中于{names}。", tilt


def _market_conclusion_copy(
    primary_value: float,
    primary_strength: str,
    allocation_state: str,
    allocation_tilt: str,
) -> str:
    """Separate total market expansion/contraction from the direction of marginal inflows."""
    if primary_strength == "flat":
        base = "市场总体平稳"
    elif primary_value > 0:
        base = {
            "small": "市场小幅扩张",
            "clear": "市场总体扩张",
            "large": "市场明显扩张",
            "extreme": "市场显著扩张",
            "generic": "市场呈净申购",
        }[primary_strength]
    else:
        base = {
            "small": "市场小幅收缩",
            "clear": "市场总体收缩",
            "large": "市场明显收缩",
            "extreme": "市场显著收缩",
            "generic": "市场呈净赎回",
        }[primary_strength]

    if allocation_state in {"unavailable", "no_inflow", "limited"}:
        return base + "。"
    if allocation_state == "dispersed" or allocation_tilt == "mixed":
        return base + "，资金流向分化。"

    primary_side = 0 if primary_strength == "flat" else (1 if primary_value > 0 else -1)
    suffixes = {
        "growth": {
            1: "，风险偏好有所回升。",
            0: "，成长方向相对活跃。",
            -1: "，但未转向防御。",
        },
        "defensive": {
            1: "，但配置仍偏防御。",
            0: "，资金略偏防御。",
            -1: "，防御倾向增强。",
        },
        "cyclical": {
            1: "，顺周期方向获得承接。",
            0: "，顺周期方向相对活跃。",
            -1: "，但顺周期方向仍有承接。",
        },
        "neutral": {
            1: "，局部配置有所增加。",
            0: "，局部方向相对活跃。",
            -1: "，局部仍有承接。",
        },
    }
    return base + suffixes.get(allocation_tilt, suffixes["neutral"])[primary_side]


def _current_regime_copy(
    trade_value: float | None,
    primary_value: float,
    trade_strength: str | None,
    primary_strength: str,
    inflow_text: str | None = None,
    allocation_state: str = "unavailable",
    allocation_tilt: str = "unknown",
) -> str:
    """Compose share -> intraday -> merged inflow ranking -> market state."""
    relation = _relation_copy(trade_value, primary_value, trade_strength, primary_strength)
    focus = inflow_text or "资金流向暂不明确。"
    market = _market_conclusion_copy(primary_value, primary_strength, allocation_state, allocation_tilt)
    return f"{relation}。{focus}{market}"


def _historical_context(primary_value: float, prior_5d_value: float | None, prior_20d_value: float | None, market_aum: float | None) -> str:
    """Compare today only with completed prior trading days, never with a window containing today."""
    if _primary_strength(primary_value, market_aum) == "flat":
        return ""

    noise_floor = max(10.0, float(market_aum or 0.0) * 0.0005)
    for n, total in ((5, prior_5d_value), (20, prior_20d_value)):
        if not isinstance(total, (int, float)) or not math.isfinite(total):
            continue
        average = float(total) / n
        if abs(average) < noise_floor:
            continue

        current = abs(primary_value)
        baseline = abs(average)
        if primary_value * average < 0:
            return (
                f"前{n}日日均{'净流入' if average > 0 else '净流出'}{baseline:.0f}亿元，"
                f"今日转为{'净流入' if primary_value > 0 else '净流出'}{current:.0f}亿元。"
            )

        ratio = current / baseline
        if ratio >= 1.8:
            return (
                f"今日{'净流入' if primary_value > 0 else '净流出'}{current:.0f}亿元，"
                f"约为前{n}日日均{'净流入' if average > 0 else '净流出'}{baseline:.0f}亿元的{ratio:.1f}倍。"
            )
        if ratio <= 0.55:
            return (
                f"今日{'净流入' if primary_value > 0 else '净流出'}{current:.0f}亿元，"
                f"低于前{n}日日均{'净流入' if average > 0 else '净流出'}{baseline:.0f}亿元。"
            )
    return ""


def _next_watch_copy(trade_value: float | None, primary_value: float, trade_strength: str | None, primary_strength: str) -> str:
    return ""


def _market_flow_headline(
    trade_value: float | None,
    primary_value: float,
    trade_turnover: float | None = None,
    market_aum: float | None = None,
    primary_5d_value: float | None = None,
    primary_20d_value: float | None = None,
    inflow_text: str | None = None,
    allocation_state: str = "unavailable",
    allocation_tilt: str = "unknown",
) -> str:
    primary_strength = _primary_strength(primary_value, market_aum)
    primary_text = _primary_copy(primary_value, primary_strength)
    if trade_value is None:
        fact_line = f"A股ETF盘中主动买卖数据暂缺；{primary_text}"
        trade_strength = None
    else:
        trade_strength = _trade_strength(trade_value, trade_turnover)
        joiner = (
            "；但"
            if trade_strength != "balanced" and trade_value * primary_value < 0 and primary_strength != "flat"
            else "；"
        )
        fact_line = f"{_trade_copy(trade_value, trade_strength)}{joiner}{primary_text}"
    current = _current_regime_copy(
        trade_value,
        primary_value,
        trade_strength,
        primary_strength,
        inflow_text=inflow_text,
        allocation_state=allocation_state,
        allocation_tilt=allocation_tilt,
    )
    return f"{fact_line}\n—— {current}"


def _visible_sector_groups(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Mutually-exclusive SW-level/theme groups actually rendered to clients."""
    return [g for g in snapshot.get("groups", []) if g.get("kind") == "industry"]


def _flow_structure_copy(snapshot: dict[str, Any]) -> str:
    """State whether the dominant direction is broad-based or structural."""
    broad = [g.get("flow1d") for g in snapshot.get("groups", []) if g.get("kind") == "broad" and isinstance(g.get("flow1d"), (int, float))]
    industry = [g.get("flow1d") for g in _visible_sector_groups(snapshot) if isinstance(g.get("flow1d"), (int, float))]
    if not broad or not industry:
        return ""

    broad_net, industry_net = sum(broad), sum(industry)
    industry_in = any(value > 0 for value in industry)
    industry_out = any(value < 0 for value in industry)
    epsilon = 0.01

    if broad_net < -epsilon:
        if industry_net > epsilon:
            return "流出以宽基为主，行业主题仍有净申购。"
        if abs(broad_net) >= abs(industry_net):
            return "流出以宽基为主，主题仍有局部申购。" if industry_in else "宽基与行业主题均有流出。"
        return "宽基与行业主题均有流出，行业主题赎回更明显。"

    if broad_net > epsilon:
        if industry_net < -epsilon:
            return "申购以宽基为主，行业主题仍有净赎回。"
        if abs(broad_net) >= abs(industry_net):
            return "申购以宽基为主，主题内部仍有分化。" if industry_out else "宽基与行业主题同步申购。"
        return "宽基与行业主题均有申购，主题流入更明显。"

    return "宽基方向接近平衡，行业主题仍有分化。" if industry_in and industry_out else ""


def _prior_primary_flows(before_day: date | None, expected_etf_count: int | float | None) -> list[float]:
    """Read completed comparable daily share-flow snapshots before the current date."""
    if before_day is None:
        return []
    history_dir = base.PUBLIC / "history"
    if not history_dir.exists():
        return []

    expected = float(expected_etf_count) if isinstance(expected_etf_count, (int, float)) and float(expected_etf_count) > 0 else None
    observations: list[tuple[date, float]] = []
    for path in history_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text("utf-8"))
            observed_day = date.fromisoformat(str(payload.get("tradeDate", "")))
            market = payload.get("market", {})
            flow = market.get("flow1d")
            count = market.get("etfCount")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if observed_day >= before_day or payload.get("status") != "verified":
            continue
        if not isinstance(flow, (int, float)) or not math.isfinite(float(flow)):
            continue
        if expected is not None:
            if not isinstance(count, (int, float)) or abs(float(count) - expected) / expected > 0.02:
                continue
        observations.append((observed_day, float(flow)))

    observations.sort(key=lambda item: item[0])
    return [flow for _, flow in observations[-20:]]


def _prior_window_total(flows: list[float], days: int) -> float | None:
    if len(flows) < days:
        return None
    return round(sum(flows[-days:]), 2)


def _regenerate_v2_conclusion(snapshot: dict[str, Any]) -> None:
    """Build the client headline from one consistent visible classification layer."""
    production._regenerate_conclusion(snapshot)
    market = snapshot["market"]
    primary_value = market.get("flow1d")
    if not isinstance(primary_value, (int, float)):
        raise ValueError("A-share stock ETF market.flow1d is required for the homepage headline")
    trade_scope = snapshot.get("flowMetrics", {}).get("secondaryMarketTradeFlow", {}).get("scopeTotals", {}).get("aShareStockEtf", {})
    raw_trade_value = trade_scope.get("netFlow1d")
    trade_value = float(raw_trade_value) if isinstance(raw_trade_value, (int, float)) and pd.notna(raw_trade_value) else None
    raw_inflow = trade_scope.get("inflow1d")
    raw_outflow = trade_scope.get("outflow1d")
    trade_turnover = None
    if isinstance(raw_inflow, (int, float)) and isinstance(raw_outflow, (int, float)) and pd.notna(raw_inflow) and pd.notna(raw_outflow):
        trade_turnover = float(raw_inflow) + float(raw_outflow)
    raw_aum = market.get("aum")
    market_aum = float(raw_aum) if isinstance(raw_aum, (int, float)) and pd.notna(raw_aum) else None
    groups = snapshot.get("groups", [])
    broad = [g for g in groups if g.get("kind") == "broad"]
    styles = [g for g in groups if g.get("kind") == "style"]
    sectors = _visible_sector_groups(snapshot)
    allocation_state, inflow_text, allocation_tilt = _inflow_focus_context(snapshot)
    flow_headline = _market_flow_headline(
        trade_value,
        float(primary_value),
        trade_turnover,
        market_aum,
        inflow_text=inflow_text,
        allocation_state=allocation_state,
        allocation_tilt=allocation_tilt,
    )

    if broad:
        broad_in_count = sum(float(g.get("flow1d", 0) or 0) > 0 for g in broad)
        broad_out_count = sum(float(g.get("flow1d", 0) or 0) < 0 for g in broad)
        broad_stat = f"共{len(broad)}组，{broad_out_count}个净流出、{broad_in_count}个净流入"
        out_groups = [g for g in sorted(broad, key=lambda g: float(g.get("flow1d", 0) or 0)) if float(g.get("flow1d", 0) or 0) < 0]
        in_groups = [g for g in sorted(broad, key=lambda g: float(g.get("flow1d", 0) or 0), reverse=True) if float(g.get("flow1d", 0) or 0) > 0]
        details = []
        if out_groups:
            details.append("净流出居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in out_groups[:2]))
        if in_groups:
            details.append("净流入居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in in_groups[:2]))
        broad_fact = f"{broad_stat}；" + "；".join(details) + "。" if details else f"{broad_stat}；当日无明显资金方向。"
    else:
        broad_fact = "暂无可分析宽基ETF。"

    if styles:
        style_in = [g for g in sorted(styles, key=lambda g: float(g.get("flow1d", 0) or 0), reverse=True) if float(g.get("flow1d", 0) or 0) > 0]
        style_out = [g for g in sorted(styles, key=lambda g: float(g.get("flow1d", 0) or 0)) if float(g.get("flow1d", 0) or 0) < 0]
        parts = []
        if style_in:
            parts.append("净流入居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in style_in[:2]))
        if style_out:
            parts.append("净流出居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in style_out[:2]))
        style_fact = "；".join(parts) + "。" if parts else f"共{len(styles)}组，当日均无明显资金方向。"
    else:
        style_fact = "暂无可分析风格ETF。"

    if sectors:
        sector_in = [g for g in sorted(sectors, key=lambda g: float(g.get("flow1d", 0) or 0), reverse=True) if float(g.get("flow1d", 0) or 0) > 0]
        sector_out = [g for g in sorted(sectors, key=lambda g: float(g.get("flow1d", 0) or 0)) if float(g.get("flow1d", 0) or 0) < 0]
        parts = []
        if sector_in:
            parts.append("净流入居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in sector_in[:2]))
        if sector_out:
            parts.append("净流出居前为" + "、".join(f"{g['name']}{float(g['flow1d']):+.1f}亿" for g in sector_out[:2]))
        sector_fact = "；".join(parts) + "。" if parts else "当日均无明显资金方向。"
    else:
        sector_fact = "暂无可分析申万一级与主题行业ETF。"

    etf_flows = [e for e in snapshot.get("etfs", []) if isinstance(e.get("flow1d"), (int, float)) and pd.notna(e.get("flow1d"))]
    single_in = max((e for e in etf_flows if float(e["flow1d"]) > 0), key=lambda e: float(e["flow1d"]), default=None)
    single_out = min((e for e in etf_flows if float(e["flow1d"]) < 0), key=lambda e: float(e["flow1d"]), default=None)
    single_parts = []
    if single_in:
        single_parts.append(f"净流入最大为{single_in['name']}{float(single_in['flow1d']):+.1f}亿")
    if single_out:
        single_parts.append(f"净流出最大为{single_out['name']}{float(single_out['flow1d']):+.1f}亿")
    single_fact = "；".join(single_parts) + "。" if single_parts else "当日无单只ETF显著资金变化。"

    facts = [broad_fact, style_fact, sector_fact, single_fact]
    snapshot["conclusion"]["facts"] = facts
    snapshot["conclusion"]["headline"] = flow_headline


def apply_v2_semantics(snapshot: dict[str, Any], day: date, share_window: list[tuple[date, pd.DataFrame]], ths: pd.DataFrame, spot: pd.DataFrame | None) -> None:
    """Apply every client-facing schema-v6 flow rule exactly once."""
    legacy_classified_count = snapshot.get("quality", {}).get("classifiedEtfCount")
    flow_model_v2.apply_flow_model(snapshot, day, share_window, ths, spot)
    flow_comparison_v2.add_primary_valuation_comparisons(snapshot)
    flow_scope_breakdown_v2.add_asset_class_totals(snapshot)
    _add_trade_net_flow(snapshot, day, ths, spot)
    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["displayName"] = "ETF当日净申购/赎回估算"
    primary["definition"] = "（T日交易所日终份额－T-1日公司行动调整后的可比份额）×T日单位净值。"
    rollups = production._build_industry_rollups(snapshot)
    snapshot["industryRollups"] = rollups
    snapshot["themeGroups"] = [g for g in snapshot.get("groups", []) if g.get("kind") == "industry" and g.get("parent")]

    market = snapshot["market"]
    classified_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in snapshot.get("groups", [])), 2)
    visible_sectors = _visible_sector_groups(snapshot)
    visible_sector_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in visible_sectors), 2)
    rollup_sector_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in rollups), 2)
    sector_in = max(visible_sectors, key=lambda g: float(g.get("flow1d", 0) or 0)) if visible_sectors else None
    sector_out = min(visible_sectors, key=lambda g: float(g.get("flow1d", 0) or 0)) if visible_sectors else None
    quality = snapshot.setdefault("quality", {})
    quality.update({
        "industryRollupCount": len(rollups), "themeGroupCount": len(snapshot["themeGroups"]),
        "legacyClassifiedEtfCountBeforeV6": legacy_classified_count, "classifiedEtfCount": len(snapshot.get("etfs", [])),
        "marketScopeEtfCount": market.get("etfCount"), "marketScope5dCount": market.get("etfCount5d"), "marketScope20dCount": market.get("etfCount20d"),
        "marketScopeSource": "同花顺精确交易日基金类型/NAV + 沪深交易所日终份额",
        "classifiedCoverageOfMarketPct": round(len(snapshot.get("etfs", [])) / max(int(market.get("etfCount") or 1), 1) * 100, 2),
        "marketScopeReconciliation": {"aShareEquityShareFlow1d": market.get("flow1d"), "classifiedGroupShareFlow1d": classified_flow, "ungroupedDifference": round(float(market.get("flow1d") or 0) - classified_flow, 2)},
        "clientSectorReconciliation": {
            "visibleGroupCount": len(visible_sectors), "visibleGroupFlow1d": visible_sector_flow,
            "industryRollupFlow1d": rollup_sector_flow, "difference": round(visible_sector_flow - rollup_sector_flow, 2),
            "topInflowGroup": None if sector_in is None else {"id": sector_in.get("id"), "name": sector_in.get("name"), "flow1d": sector_in.get("flow1d")},
            "topOutflowGroup": None if sector_out is None else {"id": sector_out.get("id"), "name": sector_out.get("name"), "flow1d": sector_out.get("flow1d")},
            "displayLayer": "mutually_exclusive_sw_level_and_theme_groups",
        },
    })
    snapshot["schemaVersion"] = 6
    snapshot.setdefault("methodology", {}).update({
        "flow": "ETF当日净流入/净流出估算 =（T日交易所日终份额 − T-1日公司行动调整后的可比份额）× T日单位净值。T-1只作为T日份额变化的基准；该结果就是T日净申购/赎回的资金估算，不是再与上一日资金流做一次比较。",
        "metricSeparation": "首页结论把两类数据翻译成易懂话术：盘中买盘/卖盘强弱来自同日成交额与外盘/内盘估算的主动买卖净额；ETF份额对应申赎资金来自交易所T日与T-1可比份额变化×T日NAV。盘中强弱按主动买卖净额占总成交额比例分为基本均衡、小幅偏强、偏强、明显占优；ETF份额资金按当日资金变化占A股股票ETF总规模比例分为基本持平、小幅、明显、大幅。结论固定按“份额主判断—盘中同步或背离—风格与行业主题合并流入排名—市场扩张或收缩及配置倾向”生成；市场总量由份额端决定，局部方向不替代市场总量判断。",
        "multiDay": "坐标轴的5日/20日字段仍为端点份额变化×期末单位净值，字段明确标记 Endpoint；首页结论的历史比较仅使用T-1及以前已落盘的逐日flow1d，且要求ETF池数量与当前日相差不超过2%，不把当天放入比较基准。",
        "scope": "首页主指标固定使用A股股票ETF范围，不含跨境股票ETF、债券ETF、货币ETF和商品ETF；同时保留全部ETF、股票ETF（含跨境）和六类资产范围用于审计与对照。",
        "valuation": "ETF当日净流入/净流出主口径使用同日单位净值；flowMetrics.primaryMarket.valuationComparisons 同时保存同一份额变化按成交均价估值的对照总额。",
        "sectorDisplay": "客户端行业结论、排名与行业资金坐标统一使用互斥的“申万一级行业+热门主题”展示层；industryRollups仅用于把热门主题回卷到申万一级行业做审计，不参与客户端最大流入/流出排名。",
        "coordinates": "横轴 = 20日相对沪深300收益率；纵轴 = 5日端点份额变化×期末NAV ÷ 5日前参考规模（%）。",
    })
    _regenerate_v2_conclusion(snapshot)


def _v2_postprocess(snapshot: dict[str, Any], day: date) -> None:
    _ORIG_POSTPROCESS(snapshot, day)
    ths = production._get_ths_day(day)
    apply_v2_semantics(snapshot, day, production._LAST_WINDOW, ths, _load_secondary_spot(day))


def daily_flow_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    return {
        "schemaVersion": 1, "tradeDate": snapshot["tradeDate"], "generatedAt": snapshot["generatedAt"],
        "metric": "primaryMarketNetSubscriptionEstimate", "valuation": "sameDayUnitNAV",
        "marketScopes": primary.get("scopeTotals", {}), "assetClassTotals": primary.get("assetClassTotals", {}),
        "valuationComparisons": primary.get("valuationComparisons", {}),
        "etfs": [{"code": item.get("code"), "name": item.get("name"), "groupId": item.get("groupId"), "shares": item.get("shares"), "previousComparableShares": item.get("previousComparableShares"), "shareDelta1d": item.get("shareDelta1d"), "nav": item.get("nav"), "flow1d": item.get("flow1d"), "flow1dAvgPriceEstimate": item.get("flow1dAvgPriceEstimate")} for item in snapshot.get("etfs", [])],
    }


def _v2_atomic_publish(snapshot: dict[str, Any]) -> Path:
    path = _ORIG_ATOMIC_PUBLISH(snapshot)
    daily_dir = base.PUBLIC / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(daily_flow_payload(snapshot), ensure_ascii=False, indent=2)
    (daily_dir / f'{snapshot["tradeDate"]}.json').write_text(text, "utf-8")
    (daily_dir / "latest.json").write_text(text, "utf-8")
    return path


def install_v2_pipeline() -> None:
    production._postprocess_snapshot = _v2_postprocess
    production.install_production_pipeline()
    base.atomic_publish = _v2_atomic_publish


def main() -> int:
    install_v2_pipeline()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
