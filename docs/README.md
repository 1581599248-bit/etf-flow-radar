# ETF Flow Radar 现行规范索引

本目录只用于标识**当前有效**的方法论与工程契约。历史的 schema v6 调研、2026-08-14/17 体检记录已从现行目录移除，但仍完整保存在 Git 提交历史中，用于审计追溯，不再作为当前系统定义依据。

## 当前唯一规范

按优先级从高到低：

1. `data_pipeline/system_contract_v7.py`：经济变量、统计范围、方向判定、累计/端点、分类边界、结论、方法论与数据溯源的机器可执行契约。
2. `data_pipeline/contract_finalizer_v7.py`：端点份额方向、端点breadth和价格代理×份额状态的确定性归一化。
3. `data_pipeline/audit_snapshot_v7.py`：任何数据允许外发前必须通过的 fail-closed 审计。
4. `README.md`：面向维护者的人类可读完整说明。
5. `data_pipeline/README.md`：生产管道、运行入口与质量门槛说明。
6. `site/index.html`：客户端唯一措辞源；构建脚本不得改写任何金融定义或标签。

## 生产入口

```bash
python data_pipeline/update_daily_v3.py
```

二级市场成交方向事实：

```bash
python data_pipeline/capture_order_flow_v3.py
```

外发审计：

```bash
python data_pipeline/audit_snapshot_v7.py
```

## 规则

若代码、JSON、页面、测试或文档出现定义冲突，以 `system_contract_v7.py` 和 `audit_snapshot_v7.py` 为最终机器判定依据；冲突本身必须被修复，不能长期保留“两个口径并存”。
