# 数据管道

生产入口统一为 `update_daily_v2.py`。旧的 `update_daily.py` 保留基础抓取/分类/发布结构，`update_daily_guarded.py`、`update_daily_resilient.py`、`update_daily_production.py` 作为已经验证过的兼容与质量保护层；外部任务和人工回放一律调用 v2 入口，避免绕过数据保护或重新引入旧资金语义。

## schema v6 核心原则

- 沪深交易所日终 ETF 份额是主源，第三方份额只能核验，不能覆盖官方份额。
- 份额拆分/合并通过“份额近似整数比例跳变 + 精确交易日 NAV 反向比例变化”确认，再把历史份额调整到可比单位。
- **一级市场主指标**：`primaryFlow1d = comparable share delta × same-day unit NAV`。
- **成交均价对照指标**：`flow1dAvgPriceEstimate = comparable share delta × same-day average traded price`，仅用于对照采用成交均价的外部统计，不能覆盖主指标。
- **二级市场主力订单流**：单独放在 `flowMetrics.secondaryMarketOrderFlow`；只有行情源日期严格等于目标交易日才写入，否则 `status=unavailable`。它不是ETF申购赎回。
- 市场范围同时保存 `allEtf`、`stockEtfIncludingCrossBorder`、`aShareStockEtf`，网站主视图使用最后一项；外部数字必须先匹配 scope 才可对账。
- 市场总量不依赖分类是否成功；分类只负责宽基、风格、申万一级行业和热门主题分组。
- 申万一级行业使用父级汇总，热门主题是子组，二者不混称。
- 5日/20日当前是 `flow5dEndpoint` / `flow20dEndpoint`，即端点份额变化×期末NAV，不是逐日资金流累计值。
- 每次发布额外落盘 `site/data/daily/YYYY-MM-DD.json`，保存单ETF的份额、可比前值、份额变化、NAV和一级市场1日净申购估算；后续真正的5/20日累计直接从每日事实表求和，不再反复联网抓历史窗口。

## 发布门槛

包括：交易所 ETF 总量、分交易所数量变化、重复代码、关键字段、21个交易日窗口、分类覆盖、NAV/成交价格覆盖、公司行动、单只极端资金变化、市场/分组对账、一级/二级市场字段隔离和三个市场范围一致性。不可解释的关键异常直接 fail closed，不覆盖 `site/data/latest.json`。

## 验证

```powershell
python -m unittest discover -s data_pipeline -p "test_*.py"
python data_pipeline/benchmark_public_flow_dates.py
python data_pipeline/update_daily_v2.py --date 2026-08-14
npm ci
npm test
```

`benchmark_public_flow_dates.py` 是独立审计脚本，用归档的交易所 T/T-1 份额与精确交易日 NAV 重算全部ETF、股票ETF（含跨境）、A股股票ETF三个一级市场范围，并单独检查二级市场订单流是否真的属于同一交易日。

AKShare 固定版本只作为公开数据采集适配层；任何接口字段变化都必须经过结构校验，不允许静默吞掉异常后缩小 ETF 样本。
