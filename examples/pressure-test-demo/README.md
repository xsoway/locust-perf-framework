# 示例：健康检查接口压力测试（找击穿点）

本示例演示如何用 `run_step_load` 对同一个接口做**压力测试**，逐步加压直到被测
服务出现**击穿点**，并产出一份标明"需修复"的中文诊断报告。它与
[`health-check-demo`](../health-check-demo/) 的区别在于：**负载测试找容量上限，
本示例专门加压到服务过载**。

被测对象是仓库内自带的**多线程限流 Mock 服务**（`mock_limited_service.py`），
当活跃并发超过 20 时返回 503，用于模拟一个"容量耗尽后开始失败的接口"——
便于稳定、可复现地演示框架对**击穿点**与**熔断**的判定。

---

## 目录内容

```text
examples/pressure-test-demo/
├── README.md                        # 本文件：复现步骤与结论
├── mock_limited_service.py          # 被测服务：多线程限流 Mock（活跃并发>20 → 503）
├── locust_health_stress.py          # 不节流压力脚本（尽力打满吞吐）
├── step_load_summary.json           # 阶梯汇总：5/10/20/40 与击穿判定
├── stats/
│   ├── step_20_users/               # 击穿前最高可承载档（0 失败）
│   │   ├── stats_stats.csv
│   │   ├── stats_stats_history.csv
│   │   ├── stats_failures.csv
│   │   └── stats_exceptions.csv
│   └── step_40_users/               # 击穿档（错误率 76%）
│       ├── stats_stats.csv
│       ├── stats_stats_history.csv
│       ├── stats_failures.csv
│       └── stats_exceptions.csv
└── report.html                      # 压力测试中文诊断报告（可直接打开）
```

---

## 复现步骤

### 1. 启动限流 Mock 被测服务

```bash
uv run python examples/pressure-test-demo/mock_limited_service.py
```

监听 `http://127.0.0.1:8099`，活跃并发 ≤ 20 返 200，> 20 返 503。

### 2. 阶梯加压找击穿点

在一个新终端执行：

```bash
uv run python -m tools.run_step_load \
  --locustfile examples/pressure-test-demo/locust_health_stress.py \
  --steps "5,10,20,40" --step-duration 8s \
  --scenario demo_health_pressure --host http://127.0.0.1:8099
```

框架会逐级加压并自动判定：

```text
   并发 |      RPS |  P95(ms) |      错误率 |      请求数
     5 |  132.9 |   32 |   0.00% |      932
    10 |  265.2 |   31 |   0.00% |     1860
    20 |  551.4 |   28 |   0.00% |     3861
    40 | 1052.4 |   29 |  76.00% |     7372
- 阶梯 40 错误率 76.00% 超过熔断阈值 30%，已提前终止。
- 击穿点：并发 40 时错误率超过 5%。
```

> 这里的击穿点判定与熔断均来自 `tools/run_step_load` 的 `detect_breakout_point`
> 与 `--abort-error-threshold`。真实场景中稳定服务会先出现**性能拐点**（TPS
> 不再随并发增长）再击穿；本例用限流 Mock 直接演示击穿判定。

### 3. 生成压力测试中文报告

用击穿档（`step_40_users`）的原始 CSV 生成诊断报告：

```bash
uv run python -m tools.report_builder \
  --scenario demo_health \
  --test-type pressure \
  --stats-csv "reports/raw/demo_health_pressure/<timestamp>/step_40_users/stats_stats.csv" \
  --history-file "reports/raw/demo_health_pressure/<timestamp>/step_40_users/stats_stats_history.csv" \
  --overview "健康检查接口压力测试：逐步加压直到服务出现过载。" \
  --plan "并发阶梯 5/10/20/40，每级 8 秒；展示击穿档（40 并发，错误率 76%）。" \
  --environment "demo(local mock 127.0.0.1:8099, 多线程限流)" \
  --api-name "健康检查接口" --method GET --path /health
```

仓库内 `report.html` 即该命令产出的成品，可直接打开。

---

## 产物说明与结论

| 阶梯 | RPS | P95(ms) | 错误率 | 说明 |
| --- | --- | --- | --- | --- |
| 5 | 132.9 | 32 | 0.00% | 正常 |
| 10 | 265.2 | 31 | 0.00% | 正常 |
| 20 | 551.4 | 28 | 0.00% | 接近容量上限 |
| **40** | **1052.4** | 29 | **76.00%** | **击穿** |

- **击穿点：并发 40**（错误率超 5% 判定），并触发熔断提前终止。
- 报告结论为 **需修复**，失败率 76%，并给出去重失败原因、补充失败明细、
  接入服务端资源监控、降低阶梯档位复核容量上限等可执行建议。

> 容量参考：本例临界在 20~40 并发之间（20 并发 551 rps 正常，40 并发过载），
> 说明该服务的实际安全承载约为 **≤ 20 活跃并发 / ~550 rps**。

---

> 说明：`reports/` 与 `logs/` 下的真实运行产物默认被 `.gitignore` 忽略；
> 本 `examples/` 目录存放的是精选脱敏教学示例，供开发者和使用者参考，重跑
> 不会覆盖仓库内示例。