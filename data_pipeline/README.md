# 数据管道

`update_daily.py` 是唯一生产入口，`classification.json` 是可审计的互斥分类表。

发布门禁：全市场 ETF 不少于 500 只、可明确分类的 A 股股票 ETF 不少于 300 只、必须取得连续 21 个真实交易日份额。收益代理覆盖低于 80% 时快照降级为 warning，关键份额门禁失败时写入 `last-failure.json`，但绝不覆盖 `latest.json`。

AKShare 仅作为采集适配层并固定版本；交易所字段才是份额主源。新浪历史行情的 JavaScript 解码器保持串行调用，避免并发线程安全问题。接口字段一旦发生变化，结构校验会使任务失败。
