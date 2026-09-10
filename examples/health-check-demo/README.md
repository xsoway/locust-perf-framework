# 示例：健康检查接口压测全流程（demo_health）

本示例演示用 `locust-perf-framework` 从零跑通一次完整的压测：

**被测对象 → 执行前预检 → 单步基准压测 → 阶梯加压找拐点 → 中文 HTML 报告。**

为避免污染真实环境并保证可复现，示例的"被测服务"是一个**本地 Mock HTTP 服务**
（`http://127.0.0.1:8099/health` 返回 200）。所有产物均为脱敏演示数据，
不包含任何真实业务域名、账号或密钥。

---

## 目录内容

```text
examples/health-check-demo/
├── README.md                        # 本文件：复现步骤与产物说明
├── data/
│   └── demo_health_payloads.csv     # 示例压测数据（1 条健康检查用例）
├── step_load_summary.json           # 阶梯加压汇总结果（含拐点判定）
├── stats/                           # 最高负载级（40 用户）Locust 原始产物
│   ├── stats_stats.csv              #   接口统计（RPS / RT / 分位数）
│   ├── stats_stats_history.csv      #   全过程趋势数据
│   ├── stats_failures.csv           #   失败聚合（本示例为空）
│   └── stats_exceptions.csv         #   异常记录（本示例为空）
└── report.html                      # 生成的中文 HTML 分析报告（可直接打开）
```

---

## 快速开始

### 1. 安装依赖

```bash
uv sync --extra dev
```

### 2. 启动本地 Mock 被测服务

任选一种方式让 `http://127.0.0.1:8099/health` 返回 200，例如用 Python 内置
`http.server`：

```bash
# 在临时目录用一个返回 200 的回调即可；也可用任意占位服务。
python -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{\"status\":\"ok\"}')
    def log_message(self, *a): pass
HTTPServer(('127.0.0.1', 8099), H).serve_forever()
"
```

> 也可使用仓库内 `config/config.ini` 的占位 host，但压测会全部失败，无法得到
> 有意义的指标。演示请始终指向本地 Mock。

### 3. 执行前预检

```bash
uv run python -m tools.preflight \
  --host http://127.0.0.1:8099 \
  --data-file examples/health-check-demo/data/demo_health_payloads.csv
```

预期输出"预检完成（未阻断）"即可继续进行。

### 4. 单步基准压测

```bash
mkdir -p reports/raw/baseline_health
uv run locust -f locustfiles/locust_demo_health_baseline.py \
  --host http://127.0.0.1:8099 \
  --headless -u 10 -r 2 -t 12s \
  --csv reports/raw/baseline_health/demo \
  --html reports/raw/baseline_health/demo.html
```

运行结束后查看控制台的汇总（请求数、失败数、RPS、RT 分位数）。

### 5. 阶梯加压找拐点

```bash
uv run python -m tools.run_step_load \
  --locustfile locustfiles/locust_demo_health_baseline.py \
  --steps "5,10,20,40" --step-duration 10s \
  --scenario demo_health --host http://127.0.0.1:8099
```

会在 `reports/raw/demo_health/<timestamp>/` 下生成每级子目录，并把汇总与拐点
判定写入 `reports/raw/demo_health/step_load_summary.json`。

> 本示例得出的结论：RPS 随并发线性增长（5→2.4、10→4.5、20→8.8、40→17.5 rps），
> P95 由 28ms 升至 180ms，**未检测到拐点**（容量未达上限，可继续加压）。

### 6. 生成中文 HTML 报告

用最高负载级（`step_40_users`）的原始 CSV 生成报告：

```bash
uv run python -m tools.report_builder \
  --scenario demo_health \
  --test-type step_load \
  --stats-csv "reports/raw/demo_health/<timestamp>/step_40_users/stats_stats.csv" \
  --history-file "reports/raw/demo_health/<timestamp>/step_40_users/stats_stats_history.csv" \
  --overview "健康检查接口阶梯加压，验证并发增长下的容量与响应瓶颈。" \
  --plan "并发阶梯 5/10/20/40，每级持续 10 秒；展示最高负载级 step=40 指标。" \
  --environment "demo(local mock 127.0.0.1:8099)" \
  --api-name "健康检查接口" --method GET --path /health
```

生成的报告位于 `reports/html/demo_health_step_load_<timestamp>.html`。
仓库 `examples/health-check-demo/report.html` 即本示例产出的报告成品，可直接用
浏览器打开查看报告结构。

---

## 产物说明与结论

| 指标（最高负载 40 用户） | 值 |
| --- | --- |
| 总请求 / 失败 | 157 / 0 |
| 吞吐量 | 17.5 req/s |
| 平均响应 / 中位数(P50) | 58.9ms / 39ms |
| P95 / P99 | 180ms / 200ms |
| 最大响应 | 206ms |
| 结论 | **通过**（失败率 0%，P95 180ms < 300ms） |

报告自带的优化建议（P0/P1/P2）演示了如何从原始指标推导出可执行的下一步：
补充失败明细采集、接入服务端资源监控、提高并发阶梯重跑以定位容量拐点。

---

> 说明：`reports/` 与 `logs/` 下的真实运行产物默认被 `.gitignore` 忽略、
> 不入库；本 `examples/` 目录存放的是精选的脱敏教学示例，供开发者和使用者
> 参考。每个示例都有自己的时间戳产物管理规则，重跑不会覆盖仓库内示例。