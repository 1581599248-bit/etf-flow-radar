# 数据管道：Data Contract 7.0

唯一生产入口为：

```bash
python data_pipeline/update_daily_v3.py
```

`update_daily.py`、`update_daily_guarded.py`、`update_daily_resilient.py`、`update_daily_production.py` 和 `update_daily_v2.py` 保留为已验证的底层采集、公司行动、容错和schema-v6事实引擎；外部任务不得直接调用这些旧入口。`update_daily_v3.py` 在底层事实形成后强制执行 `system_contract_v7.py` 与 `contract_finalizer_v7.py`，再允许发布。

## 生产链

```text
沪深交易所日终ETF份额
        ↓
精确交易日NAV / 基金类型
        ↓
公司行动识别与可比份额调整
        ↓
一级市场1日净申购/赎回金额估算
        ↓
A股/跨境/债券/货币/商品/其他互斥范围
        ↓
唯一份额方向字段 shareDirection1d
        ↓
保守研究分类：明确归组 / ambiguous
        ↓
逐日累计与端点变化严格分离
        ↓
二级市场成交方向独立命名
        ↓
结论、方法论、provenance
        ↓
audit_snapshot_v7.py
        ↓
客户端构建与语义测试
        ↓
仅通过全部闸门后发布
```

## 一级市场主事实

```text
primaryFlow1d
= (T日交易所清算后份额 - T-1日公司行动调整后的可比份额)
  × T日单位净值
```

- 交易所份额为主源。
- 第三方份额只能交叉核验，不能覆盖交易所值。
- 同日NAV为唯一主估值价格。
- 成交均价只做外部统计口径对照。
- 数据日期必须精确等于目标交易日。

## 份额方向

所有1日方向只数共用：

```text
shareDirection1d = increase | decrease | unchanged
```

绝对份额差小于0.5份统一视为浮点噪声并记为 `unchanged`。市场、资产类别、研究组不得独立重新判断符号。

5日/20日端点方向同样使用统一容忍度，并由 `contract_finalizer_v7.py` 生成确定性的 endpoint breadth 字段。

## 多日指标

### 逐日累计

真正的5日/20日累计净申购/赎回，只允许使用每日一级市场事实求和：

```text
sum(primaryFlow1d)
```

要求对应5/20个官方交易日事实完整。缺一日即 `insufficient_verified_daily_history`，客户端显示“数据积累中”。

### 端点变化

```text
flow5dEndpoint
flow20dEndpoint
```

定义为期末份额减期初可比份额后乘期末NAV。端点指标只用于存量变化和价格×份额坐标，不属于逐日累计资金流。

## 二级市场成交统计

收盘后唯一采集入口：

```bash
python data_pipeline/capture_order_flow_v3.py
```

文件仍存放在：

```text
site/data/order_flow/YYYY-MM-DD.json
```

主字段为：

- `buyInitiatedEstimate1d`
- `sellInitiatedEstimate1d`
- `aggressorImbalance1d`
- `vendorMainOrderNet1d`

其中 `aggressorImbalance1d` 是成交额按外盘/内盘主动成交量占比拆分后的主动成交方向差额。它不代表市场净新增资金，也不是ETF一级市场净申购/赎回。`vendorMainOrderNet1d` 是行情商“主力净额”原字段，作为另一项独立交易统计保存。

## 研究分类

市场总量与研究分组完全分离：

- A股股票ETF只要资产范围、份额和NAV有效，就进入市场总量。
- 是否分类成功不得影响市场总量。
- 研究分组当前主要依据基金名称规则。
- 泛化关键词或无法唯一判断暴露方向的ETF标记 `ambiguous`，继续留在市场总量，但不进入分组结论。
- 客户端统一称“研究分组”，不宣称是基金管理人、指数公司或申万官方分类。

## 价格 × 份额状态

当前价格端仍使用组内代表ETF作为价格代理：

- 横轴：代表ETF近20日相对沪深300收益。
- 纵轴：5日端点份额变化金额估算占5日前参考规模比例。

`priceFlowState` 的现行语义是：

- 跑赢基准 · 份额增加
- 跑输基准 · 份额增加
- 跑赢基准 · 份额减少
- 跑输基准 · 份额减少

禁止再使用“跑赢且流入”等会把端点份额变化误写成资金流的旧词。

## 自动任务

### 15:35 / 16:05 / 16:35

`.github/workflows/capture-etf-order-flow.yml`

保存同日二级市场成交方向事实。

### 17:30 / 18:30 / 20:00

`.github/workflows/postclose-etf-publish.yml`

若交易所清算后份额和同日NAV已齐全，则运行 `update_daily_v3.py` 并发布；否则不生成错误快照。

### 05:00 / 06:00 / 07:00 / 08:00

`.github/workflows/daily-etf-data.yml`

继续补抓上一交易日完整数据。所有生产路径最终都进入同一个v3入口和v7审计。

## 发布门槛

生产发布必须同时通过：

1. Python全量单元测试。
2. 交易所份额日期、数量、重复代码和单位检查。
3. 公司行动确认与异常变化保护。
4. NAV正值、日期一致性和资产范围检查。
5. A股市场总量与六类互斥资产桶对账。
6. `shareDirection1d` 与所有breadth只数一致。
7. ambiguous分类不得泄漏到客户端研究组。
8. 5/20日累计与端点语义严格分离。
9. 二级市场成交方向不得覆盖或命名成一级市场资金流。
10. 结论、方法论与provenance一致。
11. `audit_snapshot_v7.py` fail-closed审计。
12. 前端源文件、构建产物和语义测试一致。

任一关键检查失败，`site/data/latest.json` 不得被覆盖。

## 验证命令

```bash
python -m compileall -q data_pipeline
python -m unittest discover -s data_pipeline -p "test_*.py"
npm ci
npm test
```

真实交易日：

```bash
python data_pipeline/update_daily_v3.py --date YYYY-MM-DD
python data_pipeline/audit_snapshot_v7.py
```

历史外部回归脚本继续保留，用于发现内部自洽测试无法识别的交易日错位、份额时点错位、统计范围错位或估值口径漂移。
