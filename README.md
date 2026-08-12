# 资金ETF流动每日跟踪

A 股 ETF 真实数据日频看板。仓库已经整理为一套单一的静态站工程：同一份源码同时用于本地预览、GitHub Pages 和 Render，构建产物不提交到 GitHub。

## 目录

- `site/`：网页源码、样式和已验证数据；
- `data_pipeline/`：AKShare 数据采集、指标计算、质量门禁和测试；
- `scripts/build-site.mjs`：生成生产目录 `dist/`；
- `.github/workflows/`：每日数据更新及 GitHub Pages 部署；
- `render.yaml`：Render Blueprint 配置。

## 数据口径

- 固定使用 `AKShare 1.18.84`，不依赖 Tushare Token；
- 份额主源为上交所、深交所日终 ETF 总份额；
- 参考价格优先采用同一交易日单位净值；
- 估算资金流 = `(T日总份额 - T-1日总份额) × T日参考价格`；
- 仅质量门禁通过时更新 `site/data/latest.json`，失败不覆盖上一份已验证快照；
- 公开份额数据不能识别投资者身份，因此不能据此判断“国家队资金”；
- 指数之间存在成分重叠，观察池合计值不等于全市场净流入。

详细来源、字段和质量检查见 [`data_pipeline/README.md`](data_pipeline/README.md)。

## 每日自动更新

GitHub Actions 在工作日北京时间 09:15 运行：执行测试，读取最近完整交易日数据，通过质量门禁后提交 `site/data/`。该提交会同时触发 GitHub Pages 和 Render 自动部署。

## 部署到 Render

推荐在 Render 控制台选择 **New → Blueprint**，连接仓库 `1581599248-bit/etf-flow-radar` 并应用根目录的 `render.yaml`。

若手工创建 Static Site，请填写：

- Branch：`main`
- Build Command：`npm ci && npm run build`
- Publish Directory：`dist`

无需环境变量、数据库或付费实例。

## 本地检查

```powershell
npm ci
npm test
python -m unittest discover -s data_pipeline -p "test_*.py"
npm run dev
```

本项目仅用于市场研究与信息展示，不构成投资建议。
