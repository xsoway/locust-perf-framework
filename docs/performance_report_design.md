# 性能测试报告设计

## 设计目标

报告面向测试、研发、服务负责人和项目相关方，核心目标是快速回答三件事：

1. 本次压测测了什么。
2. 核心指标是否达标。
3. 下一步应该关注什么风险或瓶颈。

报告风格参考 `locust-perf-framework/reports/image-url-20260623-190208-cn-report-20260623-190610.html`，采用中文台账风布局：顶部报告章、关键指标账本、分区说明、接口表格和结论建议。

## 通用报告结构

所有测试类型共用以下章节：

- 测试概要：场景名称、测试类型、业务背景、执行环境、执行人、生成时间、结论。
- 测试类型与方案设计：为什么压测、采用什么压测模型、成功标准是什么。
- 参数设置：并发用户数、启动速率、压测时长、执行环境。
- 测试数据：数据文件路径、数据用途、敏感信息处理说明。
- 被测接口信息：接口名称、请求方法、目标域名、请求路径、成功判定。
- 指标说明：根据测试类型展示最少但关键的指标解释。
- Locust 统计汇总：从 `*_stats.csv` 解析接口级请求数、失败数、平均响应、P95、P99、RPS。
- 结论与建议：按失败率和响应耗时给出通过、需关注、需修复。

## 基准测试报告指标

基准测试用于建立单接口或核心场景的性能基线，指标不宜过多，优先保留：

| 指标 | 来源 | 用途 |
| --- | --- | --- |
| 总请求数 | Locust `Request Count` | 判断样本量是否足够 |
| 失败率 | `Failure Count / Request Count` | 基准测试中应接近 0 |
| 平均响应 | `Average Response Time` 加权汇总 | 建立日常对比基线 |
| P95 响应 | Locust `95%` | 后续回归的主要阈值参考 |
| P99 响应 | Locust `99%` | 观察尾部慢请求 |
| 吞吐量 | Locust `Requests/s` | 辅助记录基准吞吐能力 |
| 最大响应 | Locust `Max Response Time` | 辅助识别偶发抖动 |

基准测试报告重点不是容量上限，而是形成可复用基线，因此报告结论需要说明是否适合作为后续回归参考。

## 负载/压力测试报告指标

负载测试验证目标并发下是否稳定，压力测试寻找瓶颈或不可用边界。两类报告共用核心指标：

| 指标 | 来源 | 用途 |
| --- | --- | --- |
| 总请求数 | Locust `Request Count` | 判断压测覆盖量 |
| 失败率 | `Failure Count / Request Count` | 判断系统是否进入不可接受状态 |
| 吞吐量 | Locust `Requests/s` | 观察目标并发下处理能力 |
| 最高 P95 | 各接口 P95 最大值 | 判断高并发下多数用户体验 |
| 最高 P99 | 各接口 P99 最大值 | 判断尾延迟风险 |
| 最大响应 | Locust `Max Response Time` | 辅助定位极端慢请求 |
| 最慢接口 | 按 P95 排序 | 聚焦优先排查对象 |

负载/压力报告重点是容量和风险判断，因此报告结论需要优先说明失败率、吞吐和慢接口。

## 默认判定规则

当前框架内置轻量判定，后续可按业务要求配置化：

- 失败率大于 1%：`需修复`
- 失败率不超过 1%，但最高 P95 大于 3000 ms：`需关注`
- 失败率不超过 1%，且最高 P95 不超过 3000 ms：`通过`

这些规则只用于报告默认展示，正式项目建议由规划 Agent 在测试方案阶段确认阈值。

## 数据来源

当前实现优先读取 Locust 原生 CSV：

- `*_stats.csv`：接口统计、响应时间、分位值、RPS。

后续可扩展读取：

- `*_failures.csv`：失败类型、失败次数。
- `*_exceptions.csv`：异常堆栈和异常次数。
- `*_stats_history.csv`：RPS、失败率、用户数、响应时间趋势。
- 服务端资源监控：CPU、内存、数据库连接、网关错误、应用日志。

## 生成命令示例

```bash
uv run python -m tools.report_builder \
  --scenario demo_health \
  --test-type baseline \
  --stats-csv reports/raw/demo_health/<timestamp>/stats_stats.csv \
  --overview "上线前健康检查接口基准摸底" \
  --plan "1 个虚拟用户，持续 10 秒，建立响应时间基线" \
  --users 1 \
  --spawn-rate "1 user/s" \
  --run-time "10s" \
  --host "https://example.com" \
  --data-file "data/examples/demo_health_payloads.csv" \
  --api-name "健康检查接口" \
  --method GET \
  --path /health \
  --success-criteria "HTTP 2xx/3xx 视为成功，5xx 视为失败"
```
