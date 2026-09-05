"""Evidence-limited strategy conclusion for the daily ETF snapshot.

The model keeps four independent concepts separate:
1. market primary flow strength: net share flow / A-share ETF AUM;
2. trading strength: intraday net active flow / gross trading flow (upstream);
3. direction strength: all same-side leaf-group flows matching the two ranked
   leader directions / A-share ETF AUM;
4. direction concentration: those matching flows / all same-side leaf flows.

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
AGGRESSIVE = {"科技成长", "成长风格", "中小盘", "医药医疗", "新能源", "制造军工"}
DEFENSIVE = {"高股息", "价值质量", "公用运输"}


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


def market_posture(primary_value, primary_strength, incoming):
    p = _primary_side(primary_value, primary_strength)
    if p < 0:
        return "市场配置略偏谨慎" if primary_strength == "small" else "市场配置整体偏谨慎"
    if p == 0:
        return "市场配置总体均衡"
    labels = set(incoming["labels"])
    if not incoming["focused"]:
        return "市场配置增量较为分散"
    if labels and labels <= AGGRESSIVE:
        return "市场配置结构偏进攻"
    if labels and labels <= DEFENSIVE:
        return "市场配置结构偏防御"
    if labels & AGGRESSIVE and labels & DEFENSIVE:
        return "市场配置结构攻守并存"
    return "市场配置结构较为均衡"


def _labels(context):
    labels = list(dict.fromkeys(display_direction(x) for x in context["labels"]))
    return "与".join(labels)


def inflow_copy(context, primary_value, primary_strength):
    if not context["total"]:
        return None
    if not context["focused"]:
        return "申购分布于多个方向"
    label, band = _labels(context), context["magnitude"]
    subject = "一级资金" if _primary_side(primary_value, primary_strength) > 0 else "局部资金"
    action = {
        "small": "小幅增配",
        "clear": "明显加码",
        "large": "大幅加码",
        "extreme": "集中大额加码",
        "generic": "增配",
    }.get(band, "增配")
    if band == "limited":
        return f"{label}获得少量承接"
    return f"{subject}{action}{label}"


def outflow_copy(context):
    if not context["total"]:
        return None
    label, band = _labels(context), context["magnitude"]
    action = {
        "limited": "略有降温",
        "small": "配置小幅降温",
        "clear": "配置明显降温",
        "large": "配置大幅降温",
        "extreme": "出现集中大额流出",
        "generic": "配置降温",
    }.get(band, "配置降温")
    return f"{label}{action}"


def relationship_close(primary_value, primary_strength, trade_value, trade_strength):
    p, t = _primary_side(primary_value, primary_strength), _trade_side(trade_value, trade_strength)
    if t is None:
        return "交易端数据暂缺，配置信号尚待确认"
    if (p, t) == (1, -1):
        return "交易端仍偏谨慎，两端风险偏好明显分化"
    if (p, t) == (-1, 1):
        return "交易端虽有承接，但份额端仍偏谨慎"
    if (p, t) == (1, 1):
        return "配置与交易形成同向支撑"
    if (p, t) == (-1, -1):
        return "配置与交易共同偏谨慎"
    if (p, t) == (1, 0):
        return "交易端尚未形成同向确认"
    if (p, t) == (-1, 0):
        return "交易端相对平稳，谨慎主要来自份额端"
    if (p, t) == (0, 1):
        return "短线买盘尚未转化为份额增量"
    if (p, t) == (0, -1):
        return "短线卖压尚未转化为份额赎回"
    return "配置与交易均缺乏明确方向"


def render_market(primary_value, primary_strength, trade_value, trade_strength, groups, aum=None):
    if groups is None:
        return f"{market_state(primary_value, primary_strength, trade_value, trade_strength)}，配置方向数据暂缺。"
    rows = eligible_groups(groups)
    if not rows:
        return f"{market_state(primary_value, primary_strength, trade_value, trade_strength)}，配置方向数据暂缺。"
    incoming, outgoing = side_context(rows, 1, aum), side_context(rows, -1, aum)
    posture = market_posture(primary_value, primary_strength, incoming)
    close = relationship_close(primary_value, primary_strength, trade_value, trade_strength)
    if not incoming["total"] and not outgoing["total"]:
        return f"{posture}，各方向份额净变动接近零；{close}。"
    if (incoming["labels"] and set(incoming["labels"]) == set(outgoing["labels"])
            and incoming["focused"] and outgoing["focused"]):
        return f"{posture}，{_labels(incoming)}内部申赎分化；{close}。"
    flows = [x for x in (inflow_copy(incoming, primary_value, primary_strength), outflow_copy(outgoing)) if x]
    return f"{posture}，{'，'.join(flows)}；{close}。"
