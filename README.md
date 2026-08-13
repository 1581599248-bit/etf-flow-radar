# 资金ETF流动每日跟踪

面向客户的 A 股 ETF 日报网站。系统只使用真实公开数据，每个工作日上午在交易所完成上一交易日清算数据发布后自动更新；GitHub Pages 与 Render 共用同一份静态构建产物。

## 日报回答什么

- 今日盘面：观察池净申赎、流入/流出方向、风格与行业切换，给出精简结论。
- 主要宽基：上证50、沪深300、中证A500、中证500、中证1000、创业板、科创50的 1/5/20 日资金摘要。
- 两张坐标图：横轴为 20 日相对沪深300收益，纵轴为 5 日净申赎强度，分别观察宽基/风格与行业板块。
- 客观补充：价格—资金状态、申赎广度、单只ETF贡献集中度、数据质量与方法边界。
- 客户交付：手机端适配，支持 2.5 倍像素密度导出高清 JPG。

## 数据与口径

| 字段 | 主源 | 用途 |
| --- | --- | --- |
| 沪市 ETF 日终份额 | 上海证券交易所，AKShare 固定版本采集 | 份额变化 |
| 深市 ETF 日终份额 | 深圳证券交易所，AKShare 固定版本采集 | 份额变化 |
| T 日单位净值 | 东方财富公开基金净值，AKShare 采集 | 估算申赎金额 |
| ETF 收盘价 | 新浪公开行情，AKShare 采集 | 1/5/20 日收益代理 |

`估算净申赎 =（期末份额 − 期初份额）× 期末已验证单位净值`。金额用于观察方向与量级，不等同基金公司的最终现金流。公开份额不包含投资者身份，因此系统不会推断“国家队”、机构、个人或做市商。

每只 ETF 只进入一个主要观察组，行业优先于风格、风格优先于宽基，避免重复计算。分类规则保存在 `data_pipeline/classification.json`，可人工审计。

## 自动更新

`.github/workflows/daily-etf-data.yml` 在工作日 09:15（Asia/Shanghai）运行：

1. 执行数据管道单元测试。
2. 获取最近 21 个真实交易日份额、当日净值与组别收益代理。
3. 执行市场覆盖、分类覆盖、历史窗口、收益代理覆盖门禁。
4. 构建并运行前端测试；失败时不覆盖上一份已验证快照。
5. 提交 `site/data/latest.json` 和按日期归档的历史快照，触发 Render 自动部署。

手工指定交易日：

```powershell
python data_pipeline/update_daily.py --date 2026-08-12
```

## 本地验证

```powershell
pip install -r data_pipeline/requirements.txt
python -m unittest discover -s data_pipeline -p "test_*.py"
npm ci
npm test
```

## Render

仓库根目录 `render.yaml` 已绑定 `main` 分支：

- Build Command: `npm ci && npm run build`
- Publish Directory: `dist`
- Auto Deploy: commit

在 Render 选择 **New → Blueprint** 并授权本仓库即可。之后 GitHub 每次验证通过的提交都会自动发布。
