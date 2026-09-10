"""Evidence-limited strategy conclusion for the daily ETF snapshot.

The model keeps four independent concepts separate:
1. market primary flow strength: net share flow / A-share ETF AUM;
2. trading strength: intraday net active flow / gross trading flow (upstream);
3. direction structure: the ranked inflow and outflow directions are compared
   before a market-style statement is made;
4. direction magnitude: each displayed direction is scaled by A-share ETF AUM.

Leaf groups are mutually exclusive. Overlapping rollups are excluded. Displayed
group flows are net estimates, not gross creations/redemptions. Primary and
secondary flows are compared as signals and are never added.
"""
from __future__ import annotations

import math

KINDS = {"broad", "style", "industry"}
FOCUSED_SHARE = 0.50
DIRECTION_BANDS = (
    (0.05, "limited"),
    (0.20, "small"),
    (0.50, "clear"),
    (1.00, "large"),
)


def direction(group):
    name = group["name"]
    if any(x in name for x in ("红利", "股息")):
        return "高股息"
    if group["kind"] == "broad":
        if any(x in name for x in ("科创", "创业", "双创")):
            return "科技成长"
        if any(x in name for x in ("中证500", "中证1000", "中证2000", "国证2000")):
            return "中小盘"
        if any(x in name for x in ("沪深300", "中证A500", "中证A50", "上证50", "中证A100", "上证180")):
            return "大盘宽基"
        return "其他宽基"
    if name == "成长":
        return "成长风格"
    for label, keywords in (
        ("金融", ("银行", "券商", "证券", "保险", "金融")),
        ("科技成长", ("科技", "半导体", "芯片", "算力", "人工智能", "机器人", "电子", "计算机", "通信", "软件", "信创", "互联网", "传媒", "游戏")),
        ("医药医疗", ("医药", "创新药", "中药", "医疗", "生物")),
        ("新能源", ("新能源", "光伏", "锂电", "储能", "电力设备", "碳中和")),
        ("资源周期", ("有色", "稀土", "煤炭", "石油", "钢铁", "化工", "建材", "建筑材料", "黄金")),
        ("消费", ("消费", "食品", "白酒", "家用电器", "零售", "社会服务", "农林牧渔", "养殖")),
        ("制造军工", ("军工", "卫星", "机械", "汽车", "驾驶")),
        ("地产基建", ("房地产", "建筑装饰")),
        ("公用运输", ("公用事业", "交通运输", "环保")),
        ("价值质量", ("价值", "质量", "现金流", "低波")),
    ):
        if any(x in name for x in keywords):
            return label
    return "其他风格" if group["kind"] == "style" else "其他行业"


def display_direction(label):
    return "成长" if label in {"科技成长", "成长风格"} else label


def eligible_groups(groups):
    """Fail closed on invalid/duplicate leaf data; never shrink silently."""
    result, seen = [], set()
    for group in groups:
        if group.get("kind") not in KINDS:
            continue
        name, value = group.get("name"), group.get("flow1d")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("conclusion group name is missing")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"conclusion flow is invalid: {name}")
        identity = group.get("id") or (group["kind"], name)
        if identity in seen:
            raise ValueError(f"duplicate conclusion group: {identity}")
        seen.add(identity)
        result.append(group)
    return result


def magnitude(amount, aum):
    """Classify an absolute direction amount by percentage of market AUM."""
    if amount <= 0:
        return "flat"
    if not isinstance(aum, (int, float)) or isinstance(aum, bool) or not math.isfinite(aum) or aum <= 0:
        return "generic"
    intensity = amount / aum * 100.0
    for ceiling, label in DIRECTION_BANDS:
        if intensity < ceiling:
            return label
    return "extreme"


def side_context(groups, sign, aum=None):
    """Use raw top-two groups for labels, then all matching groups for magnitude."""
    rows = sorted(
        (g for g in groups if sign * g["flow1d"] > 0),
        key=lambda g: (-sign * g["flow1d"], g["name"], g["kind"]),
    )
    total = sum(abs(g["flow1d"]) for g in rows)
    labels = list(dict.fromkeys(direction(g) for g in rows[:2]))
    represented = sum(abs(g["flow1d"]) for g in rows if direction(g) in labels)
    return {
        "labels": labels,
        "total": total,
        "represented": represented,
        "share": represented / total if total else 0.0,
        "focused": bool(total and represented / total >= FOCUSED_SHARE),
        "magnitude": magnitude(represented, aum),
    }


def _primary_side(value, strength):
    return 0 if strength == "flat" or value == 0 else (1 if value > 0 else -1)


def _trade_side(value, strength):
    if value is None:
        return None
    return 0 if strength == "balanced" or value == 0 else (1 if value > 0 else -1)


def market_state(primary_value, primary_strength, trade_value, trade_strength):
    p, t = _primary_side(primary_value, primary_strength), _trade_side(trade_value, trade_strength)
    if t is None:
        return "市场风向暂缺交易端确认"
    return {
        (1, 1): "市场资金偏向增配",
        (-1, -1): "市场资金偏向收缩",
        (1, -1): "市场资金流向分化",
        (-1, 1): "市场资金流向分化",
        (1, 0): "市场配置端偏向增配",
        (-1, 0): "市场配置端偏向收缩",
        (0, 1): "市场偏交易性买入",
        (0, -1): "市场偏交易性卖出",
        (0, 0): "市场资金方向暂不明朗",
    }[p, t]


def total_allocation_posture(primary_value, primary_strength):
    """Describe the aggregate primary-market allocation amount, not its destination.

    A small net redemption remains "市场配置略偏谨慎" even when the
    surviving positive flows favour 科创、券商 or other high-beta directions.
    Those destinations are described separately by ``structural_copy``.
    """
    p = _primary_side(primary_value, primary_strength)
    if p < 0:
        return "市场配置略偏谨慎" if primary_strength == "small" else "市场配置整体偏谨慎"
    if p == 0:
        return "市场配置总体均衡"
    return None


def market_posture(primary_value, primary_strength, incoming=None):
    """Describe total primary-market allocation, never infer style from leaders.

    The top two inflow groups are a ranking, not a proof that all money is
    moving in that style.  In particular, a 科创50 subscription and a
    semiconductor redemption can coexist.  The structural sentence below
    handles that conflict explicitly; this sentence only states the verified
    aggregate share-flow state.
    """
    total_posture = total_allocation_posture(primary_value, primary_strength)
    if total_posture:
        return total_posture
    return {
        "small": "市场配置小幅扩张",
        "clear": "市场配置明显扩张",
        "large": "市场配置大幅扩张",
        "extreme": "市场配置显著扩张",
    }.get(primary_strength, "市场配置略有扩张")


def _displayed_labels(context):
    """Keep the order of the published top-two ranking, while merging aliases."""
    return list(dict.fromkeys(display_direction(label) for label in context["labels"]))


def _label_amount(rows, sign, label):
    return sum(
        abs(group["flow1d"])
        for group in rows
        if sign * group["flow1d"] > 0 and display_direction(direction(group)) == label
    )


def _direction_action(label, amount, aum, verb):
    """Render a direction with a scale-aware qualifier and no causal language."""
    band = magnitude(amount, aum)
    qualifier = {
        "limited": "略有",
        "small": "小幅",
        "clear": "明显",
        "large": "大幅",
        "extreme": "明显",
    }.get(band, "")
    if verb == "获得承接":
        return f"资金{qualifier}承接{label}"
    return f"{label}{qualifier}{verb}"


def structural_copy(rows, incoming, outgoing, aum):
    """Explain ranked directions without turning a local inflow into market beta.

    The headline's second sentence retains the literal top-two inflow/outflow
    rankings.  This function translates those leaders into a non-overlapping
    market read: an overlap means the same broad direction has subscriptions
    and redemptions at once, so it must be called internal divergence rather
    than 'aggressive' or 'defensive'.
    """
    incoming_labels = _displayed_labels(incoming)
    outgoing_labels = _displayed_labels(outgoing)
    shared = [label for label in incoming_labels if label in outgoing_labels]
    incoming_only = [label for label in incoming_labels if label not in outgoing_labels]
    outgoing_only = [label for label in outgoing_labels if label not in incoming_labels]
    parts = []
    if shared:
        parts.append(f"{'与'.join(shared)}内部申赎分化")
    if incoming_only:
        label = "与".join(incoming_only)
        amount = sum(_label_amount(rows, 1, item) for item in incoming_only)
        parts.append(_direction_action(label, amount, aum, "获得承接"))
    if outgoing_only:
        label = "与".join(outgoing_only)
        amount = sum(_label_amount(rows, -1, item) for item in outgoing_only)
        parts.append(_direction_action(label, amount, aum, "降温"))
    return "，".join(parts)


def relationship_close(primary_value, primary_strength, trade_value, trade_strength):
    p, t = _primary_side(primary_value, primary_strength), _trade_side(trade_value, trade_strength)
    if t is None:
        return "交易端数据暂缺，配置信号尚待确认"
    if (p, t) == (1, -1):
        return "盘中卖压未转化为整体赎回"
    if (p, t) == (-1, 1):
        return "盘中买盘未转化为整体申购"
    # 同向（包括两端均衡）已由前两句和市场配置描述完整表达；
    # 再追加“共同偏谨慎/同向支撑”只会重复，不提供新的市场判断。
    if p == t:
        return None
    if (p, t) == (1, 0):
        return "盘中尚未形成同向买盘"
    if (p, t) == (-1, 0):
        return "盘中尚未形成同向卖压"
    if (p, t) == (0, 1):
        return "短线买盘尚未转化为份额增量"
    if (p, t) == (0, -1):
        return "短线卖压尚未转化为份额赎回"
    return "配置与交易均缺乏明确方向"


def _with_relationship(body, close):
    """Append a relationship interpretation only when it adds information."""
    return f"{body}；{close}。" if close else f"{body}。"


def render_market(primary_value, primary_strength, trade_value, trade_strength, groups, aum=None):
    if groups is None:
        return f"{market_state(primary_value, primary_strength, trade_value, trade_strength)}，配置方向数据暂缺。"
    rows = eligible_groups(groups)
    if not rows:
        return f"{market_state(primary_value, primary_strength, trade_value, trade_strength)}，配置方向数据暂缺。"
    incoming, outgoing = side_context(rows, 1, aum), side_context(rows, -1, aum)
    posture = market_posture(primary_value, primary_strength)
    close = relationship_close(primary_value, primary_strength, trade_value, trade_strength)
    if not incoming["total"] and not outgoing["total"]:
        return _with_relationship(f"{posture}，各方向份额净变动接近零", close)
    structure = structural_copy(rows, incoming, outgoing, aum)
    if not structure:
        structure = "各方向份额净变动接近零"
    return _with_relationship(f"{posture}，{structure}", close)
