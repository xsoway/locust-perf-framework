# performance_workflow.md · 性能测试执行流程

## 文档定位

本文件是性能测试执行流程手册，承载需求确认、方案设计、脚本开发、执行记录、报告生成与 LLM 辅助分析的细化流程。

在当前项目中执行任何相关任务时，必须先遵循项目根目录 `AGENTS.md` 中的硬性规则；本文件用于补充流程、信息清单和角色输出要求。

## 基本原则

本框架服务于 Python + Locust 性能测试全流程：需求确认、方案设计、脚本开发、执行记录、报告生成与 LLM 辅助分析。

所有 Agent 必须先理解业务目标，再生成脚本或报告。信息不足时，优先补齐关键问题，不要直接臆造压测方案。

## 脚本命名规则

格式：

```text
locust_<业务域>_<接口或场景>_<测试类型>.py
```

示例：

```text
locust_order_create_load.py
locust_login_sms_peak.py
locust_vehicle_search_baseline.py
```

测试类型建议使用：

- `baseline`: 基准测试
- `load`: 负载测试
- `stress`: 压力测试
- `stability`: 稳定性测试
- `peak`: 峰值测试

## 报告命名规则

原始报告目录：

```text
reports/raw/<场景名>/<YYYYMMDD_HHMMSS>/
```

HTML 分析报告：

```text
reports/html/<场景名>_<测试类型>_<YYYYMMDD_HHMMSS>.html
```

分析中间产物：

```text
reports/analysis/<场景名>_<测试类型>_<YYYYMMDD_HHMMSS>.md
reports/analysis/<场景名>_<测试类型>_<YYYYMMDD_HHMMSS>.json
```

## 重跑保护

- 每次运行必须创建新的时间戳目录。
- 如果文件已存在，必须追加 `_01`、`_02` 等序号。
- 不允许覆盖历史报告、原始 CSV、日志和分析结果。

## 日志规则

- 框架日志统一写入 `logs/`。
- 日志文件格式：`<模块名>_<YYYYMMDD>.log`。
- 日志内容至少包含：开始时间、场景名称、测试类型、输入文件、输出文件、异常栈、执行结论。
- 关键路径禁止只使用 `print`。

## 数据规则

- 测试数据必须独立放在 `data/`。
- 敏感信息使用环境变量或本地未提交配置，不写入数据文件。
- 数据文件命名必须表达业务域、接口或场景、用途。

## 规划 Agent 必问信息

- 业务背景：为什么要压测，系统处于上线前、版本迭代、活动保障还是问题复现。
- 被压测接口：URL、方法、请求头、认证方式、请求体、参数含义。
- 压测类型：基准、负载、压力、稳定性、峰值，允许组合。
- 并发模型：用户数、启动速率、持续时间、阶段式加压方式。
- 测试数据：数据来源、账号池、参数池、是否可重复使用。
- 指标定义：QPS、TPS、P50/P90/P95/P99、错误率、超时率、业务成功率、资源指标。
- 退出条件：达到错误率阈值、响应时间阈值、系统不可用或资源打满。
- 报告受众：研发、测试、产品、运维或管理层。

## 脚本开发 Agent 输出要求

- 输出 Locust 脚本、数据文件模板、运行命令和日志路径。
- 脚本必须包含请求校验和失败原因记录。
- 对稳定性、压力、峰值测试必须说明运行参数。
- 不确认接口细节时，只能生成模板，不得伪造真实业务请求。

## 报告分析 Agent 输出要求

- 读取 `reports/raw/` 中指定执行批次。
- 解析 Locust CSV 指标和失败信息。
- 可调用 `config/ai_apiclient.py` 生成辅助分析，但最终报告必须保留原始指标证据。
- HTML 报告必须包含：背景、方案、指标总览、接口明细、错误分析、瓶颈判断、结论建议。
- 基准测试报告优先展示：总请求数、失败率、平均响应、P95、P99、吞吐量、最大响应。
- 负载/压力测试报告优先展示：总请求数、失败率、吞吐量、最高 P95、最高 P99、最大响应、最慢接口。
- 报告结构必须包含：测试概要、测试类型、测试方案设计、参数设置、测试数据、被测接口信息、指标说明、Locust 统计汇总、结论建议。

## 执行工具

### 阶梯加压（找拐点）`tools/run_step_load.py`

容量/压力测试建议用**阶梯加压找拐点**，而不是一次性打满。逐级升压，TPS 不再随并发增长的那一级即为**性能拐点**；错误率超阈值的那一级为**击穿点**。

```bash
uv run python -m tools.run_step_load \
  --locustfile locustfiles/locust_demo_health_baseline.py \
  --steps "10,30,50,80,100" --step-duration 30s \
  --scenario demo_health --host https://your-service.example.com
```

每级独立输出 CSV（`reports/raw/<场景>/<时间戳>/step_<并发>_users/`），末尾打印阶梯汇总表、拐点与击穿点，并把 `step_load_summary.json` 落盘。错误率超过 `--abort-error-threshold`（默认 30%）自动熔断停止后续阶梯。

### 执行前预检（Preflight）`tools/preflight.py`

正式压测前检查常见错误（目标 host 是否为占位符、数据文件是否含占位密钥、环境对齐提醒）：

```bash
uv run python -m tools.preflight \
  --host https://your-service.example.com \
  --data-file data/examples/demo_health_payloads.csv
```

存在 ERROR 级问题返回码 2 并阻断；WARN/INFO 仅提示。

## 瓶颈分析与复验

- `docs/performance_known_issues.md`：常见性能瓶颈模式库（慢 SQL、连接池耗尽、Redis 热点、GC 风暴、线程池耗尽、压测机自身瓶颈等），供定位与报告分析参考。
- 报告分析 Agent（`agents/report_analysis_agent.md`）遵循"全链路排查军师"纪律：**证据链、反证法、药方 + 复验方案**。
- 调优后请用**相同压力复测**验证（可复用同一批次的并发/时长/数据），避免"没验证就认为修好了"。

## 多源监控数据接入（可选）

瓶颈往往是**多层联动**的。除 Locust 客户端侧指标外，可接入服务器资源与慢查询数据，
在报告中叠加"资源与吞吐关联曲线"，作为交叉验证的**第二层证据**。

支持两类输入（均不触网，仅本地解析）：

- Prometheus 资源时序 CSV：列含 `timestamp(time/t)`、`cpu(cpu_usage)`、`memory(mem_usage)`、`rps(qps/tps)` 等；
- 慢查询 JSONL：每行 `{"timestamp": "2026-06-24T17:00:00", "duration_ms": 1200, "query": "SELECT ..."}`。

```bash
uv run python -m tools.report_builder \
  --scenario demo_health --test-type stress \
  --stats-csv reports/raw/demo_health/<ts>/stats_stats.csv \
  --overview "带监控关联的压测报告" --plan "阶梯加压" \
  --monitoring-csv /path/to/prometheus_metrics.csv \
  --slow-query-file /path/to/slow_queries.jsonl
```

报告会新增"资源与吞吐关联分析"板块（资源曲线 + 慢查询 Top 5）。也可用
`tools/monitoring_parser.py` 的 `parse_prometheus_csv` / `parse_slow_query_jsonl` /
`align_timeline` 自行解析后传给 `build_report(monitoring_series=...)`。
