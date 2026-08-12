# ETF资金雷达

面向证券研究与机构投资者的 A 股 ETF 资金行为研究系统。首版聚焦 ETF 份额监控、国家队代理资金、资金轮动与历史分位四个问题。站点中的示例数据均清晰标注为 `DEMO DATA`，不得用于投资决策。

## Architecture

当前 Sites 版本采用 vinext、React 19 与 TypeScript 构建可交互研究终端。产品层按“指数 → ETF”组织，首页默认展示指数聚合视图，详情抽屉下钻到基金贡献。数据接入层预留给后续 FastAPI / AKShare 服务；生产环境应通过 API 替换示例数据，页面组件不负责金融计算。

推荐的完整生产结构：

```text
app/                 Sites 前端与交互
components/          通用研究组件
features/            资金监控业务模块
services/            API 客户端
backend/             FastAPI 服务（后续）
data_pipeline/       采集、清洗、映射与指标计算（后续）
database/            ETF Master 与历史数据（后续）
tests/               计算与渲染测试
```

核心口径：`Estimated Flow_t = (Shares_t - Shares_{t-1}) × ReferencePrice_t`。参考价格优先采用当日净值，不可得时采用收盘价。ETF 份额与 ETF 规模严格区分，同一指数下主要 ETF 需完整映射后聚合。

## Local development

需要 Node.js 22.13 或更高版本。

```bash
npm install
npm run dev
npm run build
```

Windows PowerShell 可直接运行：

```powershell
$env:WRANGLER_LOG_PATH='.wrangler/wrangler.log'; npx vinext dev
$env:WRANGLER_LOG_PATH='.wrangler/wrangler.log'; npx vinext build
```

## Data source and update plan

生产版计划采用 AKShare 等公开接口，并对基金公告或交易所来源交叉核验。每日收盘后拉取 ETF 份额、净值/行情，执行清洗、指数映射、份额变化、估算资金流、历史分位、Z-Score、国家队代理指标与轮动计算，质量校验通过后写入数据库。接口不可用时不得生成伪造值，前端必须展示来源、日期与质量状态。

## Disclaimer

本平台数据及指标仅用于市场研究与信息展示，不构成任何投资建议。ETF资金流为根据基金份额变化及相关市场数据计算的估算结果。国家队代理资金指标仅用于观察特定宽基ETF的资金行为，不代表对实际投资主体身份的确认。
