# ETF资金流口径调研与 schema v6 决策（2026-08-17）

## 结论先行

这次调研最重要的发现不是“应该把8月14日硬改成某一个200多亿元”，而是：**市场上被简称为“ETF资金流”的数据至少同时混用了经济变量、统计范围和估值方法三个维度。** 如果数据结构只有一个 `flow` 字段，系统迟早会再次把不同口径混在一起。

因此 schema v6 不再追求一个脱离定义的“全市场ETF资金流”数字，而是先保存底层事实，再明确回答：

1. 是一级市场申购赎回，还是二级市场成交订单流？
2. 是全部ETF、股票ETF（含跨境），还是A股股票ETF？
3. 同一份额变化用单位净值估值，还是用成交均价估值？

只有三个维度一致，才能把本站数据与 Wind、Choice、iFinD、证券之星等公开数字做逐项对账。

## 一、公开资料里至少存在两种不同“资金流”

### A. 一级市场净申购/赎回

这是本项目最初真正想研究的变量：ETF场内份额增加代表净申购，减少代表净赎回。

公开资料常见两种估值写法：

- Wind/证券之星类：`(T日场内流通份额 - T-1日场内流通份额) × T日ETF成交均价`。
- Choice/部分净申购栏目：先统计净申购/赎回份额，再按单位净值估算金额。

两者使用的是**同一份额变化事实**，差别只是估值价格，不应被系统当成两个独立资金事件。

公开样本可验证这种写法。例如证券之星的ETF观察页面直接披露公式；中国基金报引用Wind时也会明确写“总份额变化，按照成交均价测算资金净流入/流出”。

### B. 二级市场主力资金/订单流

同花顺、东方财富等行情体系还会给出“资金净流入”“主力净流入”“超大单/大单/中单/小单”等成交订单统计。这反映二级市场买卖成交行为，并不会改变ETF总份额，因此**不能用来替代ETF申购赎回**。

同一ETF同一天可以出现一级市场净赎回，同时二级市场主力净流入，二者并不矛盾。

## 二、统计范围会把同一天变成完全不同的数字

公开媒体至少常见：

- 全部场内ETF；
- 非货币ETF；
- 股票ETF（很多Wind报道明确“含跨境ETF”）；
- 股票型ETF（某些iFinD文章又把“跨境ETF”单独列类）；
- A股股票ETF；
- 宽基/行业/主题/策略等子集合。

例如公开的2026年7月14日 iFinD 摘要同时给出：全市场ETF净流入239.10亿元、股票型ETF净流入191.56亿元，同时另列债券、跨境、货币和商品ETF。由此可见，“全市场ETF”和“A股股票ETF”不能直接拿一个总额对比。

schema v6 因此固定保存三个可比较范围：

- `allEtf`
- `stockEtfIncludingCrossBorder`
- `aShareStockEtf`

并额外保存六个互斥资产桶：

- `aShareStockEtf`
- `crossBorderStockEtf`
- `bondEtf`
- `moneyEtf`
- `commodityEtf`
- `otherEtf`

六类加总必须回到账面 `allEtf`，避免“范围不知道少了什么”的黑箱。

## 三、为什么不能为了对齐“网上200多亿”强行改数据

截至本次调研时，搜索引擎尚未稳定索引出可核验的 **2026-08-14** Wind/Choice/iFinD完整明细页。能检索到的大量“8月14日”结果实际是2025年，因此不能把旧年份数字误当成2026年基准。

我们对仓库中已经归档的2026-08-13/14交易所份额做了独立重算，不读取发布后的 `market.flow1d`：

- 可用NAV的全部场内ETF：约 -29.70亿元；
- 股票ETF（含跨境）：约 -59.10亿元；
- A股股票ETF：约 -48.30亿元；
- 588710公司行动调整后：约 -1.26亿元。

这说明在**当前已归档的交易所日终份额**之下，仅仅改变“全部ETF/股票ETF/A股股票ETF”范围，无法自然产生 -200亿元。因此如果权威2026-08-14数据确认是约 -200亿元，下一步应查的是：

1. 它是否实际为二级市场订单流；
2. Wind/iFinD使用的场内流通份额是否与交易所此处归档字段存在不同确认时点；
3. 交易日标签是否存在T/T+1归属差异；
4. 是否包含场外联接、LOF或其他本站未纳入证券类型；
5. 是否是多日累计被二次传播为单日。

**绝不能在没有逐只ETF可核验明细时，把全市场总额乘系数或人为塞进200亿元。**

## 四、已知公开日期作为外部回归基准

为避免系统只做“自己验证自己”，新增 `benchmark_known_public_20260731.py`。

公开2026-07-31数据中，iFinD报道股票ETF约净流出243.75亿元，Wind口径股票ETF（含跨境）约净流出250.50亿元；iFinD还给出了若干单ETF净流出金额。脚本会尝试从交易所历史份额重算同一天，并逐只对照已知ETF。

GitHub Hosted Runner 对上交所历史接口存在偶发403/空JSON，因此该诊断在上游不可用时只输出 `UNAVAILABLE`，不会把接口失败包装成“验证通过”。未来本地不可变原始数据缓存建立后，这项外部回归应成为每日CI的一部分，不再依赖历史联网接口。

## 五、最终数据结构

### 一级市场

```text
flowMetrics.primaryMarket
  metric = primaryMarketNetSubscriptionEstimate
  valuation = sameDayUnitNAV
  scopeTotals
    allEtf
    stockEtfIncludingCrossBorder
    aShareStockEtf
  assetClassTotals
    aShareStockEtf
    crossBorderStockEtf
    bondEtf
    moneyEtf
    commodityEtf
    otherEtf
  valuationComparisons
    sameDayAverageTradedPrice
```

单ETF同时保存：

```text
shares
previousComparableShares
shareDelta1d
nav
primaryFlow1d
flow1dAvgPriceEstimate
```

主展示采用NAV估值，成交均价估值只做Wind类外部对照。这样如果两个总额不同，可以明确知道是估值差异，而不是底层份额数据发生了变化。

### 二级市场

```text
flowMetrics.secondaryMarketOrderFlow
  metric = secondaryMarketMainOrderFlow
  status = available | unavailable
  tradeDate
  scopeTotals
```

二级市场字段不再从隔夜“当前行情”倒推。新增15:35/16:05/16:35收盘后轻任务，在交易日当天把同日订单流保存为不可变文件：

`site/data/order_flow/YYYY-MM-DD.json`

隔夜生产任务只读取这个同日文件；没有同日文件就显示 unavailable。

## 六、5日/20日改造

旧代码把：

`(T份额 - T-5/T-20份额) × T价格`

叫作“累计资金流”，这是不准确的。schema v6明确改名为 `flow5dEndpoint` / `flow20dEndpoint`。

同时新增每日事实表：

`site/data/daily/YYYY-MM-DD.json`

以后真正5日/20日累计净申购额直接：

`sum(primaryFlow1d)`

这样既更准确，也不需要每天重新联网抓21个历史交易日的份额，能明显降低限流和运行时间。

## 七、公开调研参考

本次主要核对了以下类型的公开资料：

- 证券之星 ETF观察：公开给出“场内流通份额变化 × 当日ETF均价”的计算公式。
- 中国基金报/证券时报引用Wind：股票ETF总份额变化、按成交均价估算资金净流入/流出，并经常明确“含跨境ETF”。
- 东方财富Choice ETF追踪：公开给出净申购/赎回份额，并按最新净值估算金额。
- 经济观察网转引同花顺iFinD：同时披露全市场ETF、股票型、债券、跨境、货币、商品等分类资金流。
- 东方财富ETF行情：提供主力净流入、超大单/大单等二级市场订单流字段。

本项目不把任何一个资讯页面的标题数字当成“真值”。正确方法是保留原始份额/NAV/价格/订单流，并让外部口径可以在数据结构中被复现和解释。
