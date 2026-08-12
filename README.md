# 资金ETF流动每日跟踪

面向 A 股 ETF 研究的真实数据日频看板。系统按“指数 → ETF”聚合交易所日终份额变化，并使用同一交易日单位净值估算资金流。网页支持桌面端、手机端、指数下钻和 3 倍像素高清 JPG 导出。

## 数据与口径

- 主采集适配器：固定版本 `AKShare 1.18.84`，无 Tushare Token 依赖；
- 份额主源：上海证券交易所、深圳证券交易所日终 ETF 总份额；
- 参考价格：同交易日单位净值，缺失时才允许使用同日收盘价；
- 指标：`Estimated Flow = (Shares_t - Shares_t-1) × ReferencePrice_t`；
- 历史不足时，5 日、20 日、250 日位置保持空值，不生成替代值；
- Critical 质量检查失败时不覆盖上一份已验证快照。

详细字段、来源和门禁见 [`data_pipeline/README.md`](data_pipeline/README.md)。

## 自动更新

GitHub Actions 在工作日北京时间 09:15 运行：

1. 安装固定版本依赖并运行确定性测试；
2. 自动识别最近有完整官方观测的交易日；
3. 拉取 SSE/SZSE 份额、同日净值与可用的同日交叉核验；
4. 通过质量门禁后原子更新 `public/data/latest.json` 并保留日历史；
5. 网站运行时读取 GitHub 最新已验证快照，更新数据无需重新发布网站。

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

本平台数据及指标仅用于市场研究与信息展示，不构成投资建议。ETF 资金流是基于份额变化与同日参考价格计算的估算值；“国家队代理”只表示预定义宽基 ETF 观察池，不确认真实投资主体。
