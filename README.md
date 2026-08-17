# 资金ETF流动每日跟踪

面向客户的 A 股 ETF 日报网站。系统只使用真实公开数据，在交易所完成上一交易日日终份额披露后自动更新；GitHub Pages 与 Render 共用同一份静态构建产物。

## 日报回答什么

- 今日一级市场：A 股股票 ETF 估算净申购/赎回、流入/流出分布、宽基、风格、申万一级行业与热门主题切换。
- 统计范围对账：同时保存“全部场内ETF”“股票ETF（含跨境）”“A股股票ETF”三个比较口径，并把全部ETF拆成A股股票、跨境、债券、货币、商品、其他六个互斥资产桶。
- 二级市场资金：若已经留存与交易日严格同日的“主力净流入”数据，单独展示为二级市场订单流；绝不与ETF净申购/赎回混为一个字段。
- 两张坐标图：横轴为 20 日相对沪深300收益，纵轴为 5 日端点份额变化估算占 5 日前参考规模的比例。
- 客观补充：价格—资金状态、ETF份额增加/减少只数、单只ETF贡献集中度与数据质量。
- 客户交付：手机端适配，支持高清 JPG 导出。

## 为什么“ETF资金流”必须拆成两个变量

市场资讯里至少有两种完全不同的数据会被简称为“ETF资金流”：

1. **一级市场净申购/赎回**：观察ETF场内流通份额是否增加或减少。Wind/证券之星等常见估算为“份额变化 × 当日成交均价”；Choice等也会按“净申购/赎回份额 × 单位净值”估值。
2. **二级市场主力资金**：按照ETF二级市场成交订单统计主动买卖或大单资金，例如东方财富行情里的“主力净流入-净额”。它反映交易订单流，不代表ETF份额申购赎回。

同一只ETF、同一天，这两个数字可以方向相反、量级不同。因此 schema v6 起禁止使用一个 `flow` 字段承载这两个概念。

## 核心数据口径（schema v6）

### 1. 一级市场一日净申购/赎回：主研究指标

主展示口径：

`primaryFlow1d =（T日日终份额 − T-1日经公司行动调整后的可比份额）× T日单位净值`

单位统一为亿元。选择同日单位净值作为主展示估值，是为了让每个历史交易日都可精确复现，并与公开“净申购份额 × 最新净值”的统计方式一致。

同时保留一个**对照估算**：

`flow1dAvgPriceEstimate = 份额变化 × T日成交均价`

它用于与采用成交均价的 Wind/资讯统计做交叉核验，但不会覆盖主字段。两个金额使用的是同一份额变化事实，只是估值方法不同。

### 2. 二级市场主力资金：单独命名空间

二级市场订单流只保存到：

`flowMetrics.secondaryMarketOrderFlow`

交易日下午由独立轻任务抓取 ETF 行情中的“主力净流入-净额”，并要求数据源的 `数据日期` 与目标交易日完全相等。成功后写入：

`site/data/order_flow/YYYY-MM-DD.json`

隔夜生产任务优先读取这个不可变同日文件。如果当天没有成功留存，历史回放显示 `unavailable`，**绝不把周末/下一自然日的行情快照回填成周五数据**。

### 3. 三个比较范围 + 六个互斥资产类别

`flowMetrics.primaryMarket.scopeTotals` 固定包含：

- `allEtf`：全部可验证场内ETF。
- `stockEtfIncludingCrossBorder`：股票ETF，包含港股/QDII等跨境股票ETF，便于对照很多 Wind/基金媒体的“股票ETF”口径。
- `aShareStockEtf`：仅A股股票ETF，是本站主研究口径。

同时 `assetClassTotals` 把全部ETF拆成：

- A股股票ETF
- 跨境ETF
- 债券ETF
- 货币ETF
- 商品ETF
- 其他ETF

六类互斥且必须加总回 `allEtf`。因此看到外部“ETF净流出200亿元”时，系统可以先判断它说的是全部ETF、股票ETF（含跨境）、A股股票ETF，还是二级市场主力订单流，再比较数字，而不是直接拿一个 headline 对一个 headline。

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

schema v6 同时新增 `site/data/daily/YYYY-MM-DD.json`，每天永久保存单ETF的 `shareDelta1d / nav / primaryFlow1d`。待积累足够历史后，真正的 5 日/20 日累计净申购额将直接使用每日 `primaryFlow1d` 求和，不再重新联网抓21天历史数据。

### 6. 行业与主题

每只 ETF 只有一个叶子归属，避免市场总量重复计算；行业展示采用两层结构：

- 申万一级行业：按 SW2021 一级行业父级汇总。
- 热门主题：半导体、芯片、消费电子、AI算力、创新药、机器人等作为一级行业下的子主题单独观察。

因此“半导体”不会被误称为“申万一级行业”，电子一级行业会完整包含其半导体/芯片/消费电子等子主题 ETF。

## 自动更新

系统现在拆成两个职责明确的任务，而不是让一个隔夜任务承担所有数据时点：

### 收盘后：保存二级市场订单流

`.github/workflows/capture-etf-order-flow.yml` 在周一至周五北京时间 **15:35、16:05、16:35** 尝试运行 `capture_order_flow_v2.py`。成功一次后当日文件即固定，后续重试直接退出；节假日自动跳过。

### 隔夜：生成一级市场净申购/赎回日报

生产入口统一为：

`python data_pipeline/update_daily_v2.py`

`.github/workflows/daily-etf-data.yml` 在周二至周六（对应周一至周五交易日）北京时间 **05:00、06:00、07:00、08:00** 检查是否出现新的完整交易所日终份额/NAV数据。两类 workflow 均使用 `cancel-in-progress: true`，避免接口缓慢时重复任务堆叠。

隔夜生产发布按以下顺序执行：

1. 单元测试。
2. 获取最新完整交易日与可比份额窗口。
3. 执行交易所份额、第三方交叉核验、公司行动、NAV/成交价格一致性、ETF总数、重复代码、分类覆盖和资金对账检查。
4. 生成 schema v6：一级市场、二级市场、三个比较范围、六个互斥资产类别、ETF级审计字段与每日单ETF缓存。
5. 构建静态页面并运行前端测试。
6. 只有通过质量门槛才更新 `site/data/latest.json`、历史快照、每日资金缓存和完整 ETF 名册；失败时保留上一份已验证数据。

如果已经发布了同一交易日，后续定时任务只做最新交易日探测并直接退出，不会重复发布。

## 数据文件

- `site/data/latest.json`：最新已验证完整快照。
- `site/data/history/YYYY-MM-DD.json`：完整历史快照。
- `site/data/daily/YYYY-MM-DD.json`：schema v6 每日单ETF一级市场资金事实表，用于未来真正的5/20日累计。
- `site/data/order_flow/YYYY-MM-DD.json`：交易日收盘后留存的二级市场ETF主力订单流事实表。
- `site/data/universe/`：交易所 ETF 完整名册与每日审计。
- `data_pipeline/classification.json`：可人工审计的分类规则。
- `docs/flow-methodology-research-20260817.md`：本次公开口径调研、数据结构决策及未决问题记录。

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
