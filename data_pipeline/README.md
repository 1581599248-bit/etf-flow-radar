# 数据管道

生产入口是 `update_daily_production.py`。`update_daily.py` 保留基础计算与发布结构，`update_daily_guarded.py`、`update_daily_resilient.py` 是兼容层；外部任务和人工回放一律调用 production 入口，避免绕过数据保护。

核心原则：

- 沪深交易所日终 ETF 份额是主源，第三方份额只能核验，不能覆盖官方份额。
- 份额拆分/合并通过“份额近似整数比例跳变 + 精确交易日 NAV 反向比例变化”确认，再把历史份额调整到可比单位。
- 1日资金流使用份额变化 × 同日成交均价；成交价格与 NAV 异常偏离时回退 NAV。
- 市场总量按 A 股股票 ETF 范围独立计算，不依赖分类是否成功；分类只负责宽基、风格、申万一级行业和热门主题分组。
- 申万一级行业使用父级汇总，热门主题是子组，二者不混称。
- 5日/20日当前是端点份额变化估算，不是逐日资金流累计值。

发布门槛包括：交易所 ETF 总量、分交易所数量变化、重复代码、关键字段、21个交易日窗口、分类覆盖、价格代理覆盖、公司行动、单只极端资金变化与市场/分组对账。不可解释的关键异常直接 fail closed，不覆盖 `site/data/latest.json`。

自动化与本地验证均使用：

```powershell
python -m unittest discover -s data_pipeline -p "test_*.py"
python data_pipeline/update_daily_production.py --date 2026-08-14
```

AKShare 固定版本只作为公开数据采集适配层；任何接口字段变化都必须经过结构校验，不允许静默吞掉异常后缩小 ETF 样本。
