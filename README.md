# 资金ETF流动每日跟踪

面向客户的 A 股 ETF 日报网站。系统只使用真实公开数据，在交易所完成上一交易日日终份额披露后自动更新；GitHub Pages 与 Render 共用同一份静态构建产物。

## 日报回答什么

- 当日成交资金：A股股票ETF当天主动买入与主动卖出相抵后的交易净额，直接显示“当日成交资金净流入/净流出”。
- ETF份额变化：A股股票ETF当日日终份额相对上一交易日日终可比份额的变化，并按当日单位净值折算为“ETF份额较上一日净流入/净流出”。
- 统计范围对账：同时保存“全部场内ETF”“股票ETF（含跨境）”“A股股票ETF”三个比较口径，并把全部ETF拆成A股股票、跨境、债券、货币、商品、其他六个互斥资产桶。
- 两张坐标图：横轴为 20 日相对沪深300收益，纵轴为 5 日端点份额变化估算占 5 日前参考规模的比例。
- 客观补充：价格—资金状态、ETF份额增加/减少只数、单只ETF贡献集中度与数据质量。
- 客户交付：手机端适配，支持高清 JPG 导出。

## 为什么首页同时展示两个数字

首页两层指标都固定为 **A股股票ETF**，不含跨境、债券、货币和商品ETF，但经济含义不同：

1. **当日成交资金净流入/净流出**：只使用T日当天的成交数据。按ETF成交额结合外盘/内盘主动成交方向拆分主动买入与主动卖出，首页只显示两者相减后的净额。
2. **ETF份额较上一日净流入/净流出**：观察T日收盘后ETF份额相对T-1日实际增加或减少多少，再按T日单位净值折算金额。T-1日只是计算T日份额变化的基准，不是再次计算“上一日资金流”。

同一天这两个数字可以方向相反、量级不同，因此禁止使用一个 `flow` 字段混合承载。东方财富行情中的“主力净流入-净额”继续保留为辅助交易指标，但不再作为首页第一层数字。

## 核心数据口径（schema v6）

### 1. 当日成交资金净流入/净流出：首页第一层

同日交易净额的基础计算为：

`tradeNetFlow1d = tradeInflow1d - tradeOutflow1d`

其中同日成交额按外盘/内盘主动成交量占比拆分：

`tradeInflow1d = 成交额 × 外盘 / (外盘 + 内盘)`

`tradeOutflow1d = 成交额 × 内盘 / (外盘 + 内盘)`

首页只显示 `tradeNetFlow1d` 的差额，并根据正负动态写成“当日交易净流入”或“当日交易净流出”。只有数据源日期与目标交易日严格一致时才发布；否则显示暂无同日数据，不做跨日回填。

该指标写入：

`flowMetrics.secondaryMarketTradeFlow`

原东方财富“主力净流入-净额”继续保存在 `flowMetrics.secondaryMarketOrderFlow`，仅作辅助，不替代全成交方向净额。

### 2. ETF份额较上一日净流入/净流出：首页第二层

主展示口径：

`shareFlow1d =（T日日终份额 − T-1日经公司行动调整后的可比份额）× T日单位净值`

单位统一为亿元。这个结果本身就是T日当天的份额资金变化；T-1只作为基准。

同时保留一个对照估算：

`flow1dAvgPriceEstimate = 份额变化 × T日成交均价`

它用于与采用成交均价的 Wind/资讯统计做交叉核验，但不会覆盖主字段。两个金额使用的是同一份额变化事实，只是估值方法不同。

### 3. 三个比较范围 + 六个互斥资产类别

`flowMetrics.primaryMarket.scopeTotals` 固定包含：

- `allEtf`：全部可验证场内ETF。
- `stockEtfIncludingCrossBorder`：股票ETF，包含港股/QDII等跨境股票ETF，便于对照很多 Wind/基金媒体的“股票ETF”口径。
- `aShareStockEtf`：仅A股股票ETF，是本站首页及主要研究口径。

`flowMetrics.secondaryMarketTradeFlow.scopeTotals` 使用相同的三个范围，因此首页第一层与第二层可以严格按同一个A股股票ETF集合比较。

同时 `assetClassTotals` 把全部ETF拆成：

- A股股票ETF
- 跨境ETF
- 债券ETF
- 货币ETF
- 商品ETF
- 其他ETF

六类互斥且必须加总回 `allEtf`。

### 4. 份额主源与公司行动

- 沪市、深市 ETF 日终份额：交易所公开数据，AKShare 仅作为采集适配层。
- 第三方“最新份额”：只做交叉核验，**不得覆盖交易所官方份额**。
- 当份额发生接近 2 倍、3 倍、1/2、1/3 等异常跳变时，系统使用精确交易日 NAV 的反向比例变化确认拆分/合并；确认后将历史份额调整到同一份额单位再计算资金流。
- 无法解释的超大单只 ETF 资金变化触发熔断，禁止覆盖上一份已验证快照。

### 5. 5日 / 20日

当前 5 日、20 日字段明确命名为：

- `flow5dEndpoint`
- `flow20dEndpoint`

计算为期末份额减去 5/20 个交易日前的可比份额，再乘期末单位净值。这属于**端点份额变化估算**，不是逐日资金流求和。

schema v6 同时新增 `site/data/daily/YYYY-MM-DD.json`，每天永久保存单ETF的 `shareDelta1d / nav / flow1d`。待积累足够历史后，真正的 5 日/20 日累计份额净流入将直接使用每日 `flow1d` 求和，不再重新联网抓21天历史数据。

### 6. 行业与主题

每只 ETF 只有一个叶子归属，避免市场总量重复计算；行业展示采用两层结构：

- 申万一级行业：按 SW2021 一级行业父级汇总。
- 热门主题：半导体、芯片、消费电子、AI算力、创新药、机器人等作为一级行业下的子主题单独观察。

因此“半导体”不会被误称为“申万一级行业”，电子一级行业会完整包含其半导体/芯片/消费电子等子主题 ETF。

## 自动更新

系统拆成两个职责明确的任务：

### 收盘后：保存同日交易资金事实

`.github/workflows/capture-etf-order-flow.yml` 在周一至周五北京时间 **15:35、16:05、16:35** 尝试运行 `capture_order_flow_v2.py`。任务保存成交额、外盘/内盘推导的当日交易净额以及主力净流入辅助字段。成功一次后当日文件即固定，后续重试直接退出；节假日自动跳过。

### 隔夜：生成ETF份额较上一日净流入/净流出日报

生产入口统一为：

`python data_pipeline/update_daily_v2.py`

`.github/workflows/daily-etf-data.yml` 在周二至周六（对应周一至周五交易日）北京时间 **05:00、06:00、07:00、08:00** 检查是否出现新的完整交易所日终份额/NAV数据。两类 workflow 均使用 `cancel-in-progress: true`，避免接口缓慢时重复任务堆叠。

隔夜生产发布按以下顺序执行：

1. 单元测试。
2. 获取最新完整交易日与可比份额窗口。
3. 执行交易所份额、第三方交叉核验、公司行动、NAV/成交价格一致性、ETF总数、重复代码、分类覆盖和资金对账检查。
4. 生成 schema v6：当日交易净额、ETF份额较上一日净流入/净流出、三个比较范围、六个互斥资产类别、ETF级审计字段与每日单ETF缓存。
5. 构建静态页面并运行前端测试。
6. 只有通过质量门槛才更新 `site/data/latest.json`、历史快照、每日资金缓存和完整 ETF 名册；失败时保留上一份已验证数据。

如果已经发布了同一交易日，后续定时任务只做最新交易日探测并直接退出，不会重复发布。

## 数据文件

- `site/data/latest.json`：最新已验证完整快照。
- `site/data/history/YYYY-MM-DD.json`：完整历史快照。
- `site/data/daily/YYYY-MM-DD.json`：schema v6 每日单ETF份额变化事实表，用于未来真正的5/20日累计。
- `site/data/order_flow/YYYY-MM-DD.json`：交易日收盘后留存的同日ETF交易资金事实表；schema v2起包含 `tradeInflow1d / tradeOutflow1d / tradeNetFlow1d`，并保留 `mainOrderFlow1d` 辅助字段。
- `site/data/universe/`：交易所 ETF 完整名册与每日审计。
- `data_pipeline/classification.json`：可人工审计的分类规则。
- `docs/flow-methodology-research-20260817.md`：公开口径调研、数据结构决策及验证记录。

## 本地验证

```powershell
pip install -r data_pipeline/requirements.txt
python -m unittest discover -s data_pipeline -p "test_*.py"
python data_pipeline/benchmark_known_public_20260731.py
python data_pipeline/benchmark_public_flow_dates.py
python data_pipeline/update_daily_v2.py --date 2026-08-14
npm ci
npm test
```

## Render

仓库根目录 `render.yaml` 绑定 `main` 分支：

- Build Command: `npm ci && npm run build`
- Publish Directory: `dist`
- Auto Deploy: commit

每次 `main` 上的数据或前端变更通过验证后，会触发 Render 与 GitHub Pages 更新。
