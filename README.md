# 资金ETF流动每日跟踪

面向 A 股 ETF 研究的真实数据日频看板。系统按“指数 → ETF”聚合交易所日终份额变化，并使用同一交易日单位净值估算资金流。网页支持桌面端、手机端、指数下钻和 2800 像素宽高清 JPG 导出。结论采用“事实—证据—限制—待确认信号”结构。

## 数据与口径

- 主采集适配器：固定版本 `AKShare 1.18.84`，无 Tushare Token 依赖；
- 份额主源：上海证券交易所、深圳证券交易所日终 ETF 总份额；
- 参考价格：同交易日单位净值，缺失时才允许使用同日收盘价；
- 指标：`Estimated Flow = (Shares_t - Shares_t-1) × ReferencePrice_t`；
- 历史不足时，5 日、20 日、250 日位置保持空值，不生成替代值；
- 头条指数池排除增强、指增、价值、成长、红利、低波和等权等策略变体；
- 交易所份额数据不能识别投资者身份，因此网站不展示“国家队代理资金”；
- Critical 质量检查失败时不覆盖上一份已验证快照。

详细字段、来源和门禁见 [`data_pipeline/README.md`](data_pipeline/README.md)。

## 自动更新

GitHub Actions 在工作日北京时间 09:15 运行：

1. 安装固定版本依赖并运行确定性测试；
2. 自动识别最近有完整官方观测的交易日；
3. 拉取 SSE/SZSE 份额、同日净值与可用的同日交叉核验；
4. 通过质量门禁后原子更新 `public/data/latest.json` 并保留日历史；
5. 数据提交触发 GitHub Pages 与 Render 自动重新发布。

## Render 部署

仓库根目录的 `render.yaml` 已配置为 Render Static Site Blueprint：构建命令为 `npm ci && npm run pages:build`，发布目录为 `pages-dist`，跟随 `main` 分支每次提交自动部署。

在 Render 控制台选择 **New → Blueprint**，连接仓库 `1581599248-bit/etf-flow-radar` 并应用即可。此静态站不需要环境变量、数据库或付费实例。

## 本地运行

需要 Node.js 22.13+ 与 Python 3.12+。

```powershell
npm install
python -m venv .venv
.\.venv\Scripts\pip.exe install -r data_pipeline\requirements.txt
.\.venv\Scripts\python.exe data_pipeline\update_daily.py
npm run lint
npm test
npm run dev
```

## 免责声明

本平台数据及指标仅用于市场研究与信息展示，不构成投资建议。ETF 资金流是基于份额变化与同日参考价格计算的估算值。观察池中的指数存在成分重叠，合计值不等于全市场净流；公开份额数据也不能确认真实投资主体。
