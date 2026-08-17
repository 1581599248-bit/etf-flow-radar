# 数据管道

生产入口统一为 `update_daily_v2.py`。旧的 `update_daily.py` 保留基础抓取/分类/发布结构，`update_daily_guarded.py`、`update_daily_resilient.py`、`update_daily_production.py` 作为已经验证过的兼容与质量保护层；外部任务和人工回放一律调用 v2 入口，避免绕过数据保护或重新引入旧资金语义。

## schema v6 核心原则

- 沪深交易所日终 ETF 份额是主源，第三方份额只能核验，不能覆盖官方份额。
- 份额拆分/合并通过“份额近似整数比例跳变 + 精确交易日 NAV 反向比例变化”确认，再把历史份额调整到可比单位。
- **一级市场主指标**：`primaryFlow1d = comparable share delta × same-day unit NAV`。
- **成交均价对照指标**：`flow1dAvgPriceEstimate = comparable share delta × same-day average traded price`，仅用于对照采用成交均价的 Wind/资讯统计，不能覆盖主指标。
- **二级市场主力订单流**：单独放在 `flowMetrics.secondaryMarketOrderFlow`。15:35、16:05、16:35 的轻量任务用 `capture_order_flow_v2.py` 在交易日当天留存；隔夜生产任务优先读取 `site/data/order_flow/YYYY-MM-DD.json`。没有严格同日数据就标记 `unavailable`，绝不拿自然日已滚动的行情快照冒充历史数据。
- 市场范围同时保存 `allEtf`、`stockEtfIncludingCrossBorder`、`aShareStockEtf`；网站主视图使用最后一项，外部数字必须先匹配 scope 才可对账。
- 资产类别同时保存六个互斥桶：`aShareStockEtf`、`crossBorderStockEtf`、`bondEtf`、`moneyEtf`、`commodityEtf`、`otherEtf`，六类加总必须严格回到 `allEtf`。货币ETF优先于债券规则识别，避免旧全局排除表达式把现金管理ETF误归债券。
- 市场总量不依赖分类是否成功；分类只负责宽基、风格、申万一级行业和热门主题分组。
- 申万一级行业使用父级汇总，热门主题是子组，二者不混称。
- 5日/20日当前是 `flow5dEndpoint` / `flow20dEndpoint`，即端点份额变化×期末NAV，不是逐日资金流累计值。
- 每次发布额外落盘 `site/data/daily/YYYY-MM-DD.json`，保存单ETF的份额、可比前值、份额变化、NAV和一级市场1日净申购估算；后续真正的5/20日累计直接从每日事实表求和，不再反复联网抓历史窗口。

## 两类自动任务

### 收盘后轻任务

`.github/workflows/capture-etf-order-flow.yml` 在北京时间 15:35、16:05、16:35 尝试保存**二级市场**ETF主力订单流。它不计算申购赎回，不需要等待交易所日终份额披露，成功一次后当日后续重试直接退出。

### 隔夜主任务

`.github/workflows/daily-etf-data.yml` 在北京时间 05:00、06:00、07:00、08:00 检查上一交易日完整的交易所份额/NAV数据，运行 `update_daily_v2.py`，计算一级市场净申购/赎回、范围对账、行业主题汇总并发布静态数据。

两类任务都使用 `cancel-in-progress: true`，避免接口缓慢时多个同类任务重叠。

## 发布门槛

包括：交易所 ETF 总量、分交易所数量变化、重复代码、关键字段、21个交易日窗口、分类覆盖、NAV/成交价格覆盖、公司行动、单只极端资金变化、市场/分组对账、一级/二级市场字段隔离、三个比较范围以及六个互斥资产类别的一致性。不可解释的关键异常直接 fail closed，不覆盖 `site/data/latest.json`。

## 验证

```powershell
python -m unittest discover -s data_pipeline -p "test_*.py"
python data_pipeline/benchmark_known_public_20260731.py
python data_pipeline/benchmark_public_flow_dates.py
python data_pipeline/update_daily_v2.py --date 2026-08-14
npm ci
npm test
```

`benchmark_public_flow_dates.py` 用归档的交易所 T/T-1 份额与精确交易日 NAV 重算全部ETF、股票ETF（含跨境）、A股股票ETF三个一级市场范围，并检查二级市场订单流是否真的属于同一交易日。

`benchmark_known_public_20260731.py` 用公开的 Wind/iFinD 已知日期作外部基准诊断，专门用于发现“交易日错位、份额时点错位、范围错位”这类仅靠内部自洽测试发现不了的问题。若上交所历史接口在 GitHub Runner 被限流，脚本只报告 `UNAVAILABLE`，不会把失败当成正确结果。

AKShare 固定版本只作为公开数据采集适配层；任何接口字段变化都必须经过结构校验，不允许静默吞掉异常后缩小 ETF 样本。
