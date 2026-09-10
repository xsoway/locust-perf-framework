# locust-perf-framework

> 基于 Python + Locust 的通用性能测试框架，沉淀「压测方案 → 脚本 → 数据 → 执行记录 → 中文分析报告 → LLM 辅助总结」的可复用流程。

本框架不是替代 Locust，而是在 Locust 之上加了一层面向测试工程落地的组织方式：统一目录边界、命名规范、防覆盖的时间戳产物管理、命令封装，以及把 Locust 原始 CSV 二次加工成可直接汇报的中文 HTML 报告。

## ✨ 特性

- 📁 **分层约定**：`locustfiles/` 脚本、`data/` 数据、`reports/` 原始+二次报告、`logs/` 日志、`config/` 配置。
- 🔒 **自动时序命名 / 不覆盖**：每次执行自动生成时间戳目录，目标已存在自动追加序号，杜绝重跑覆盖历史。
- 🚀 **一体化命令**：面对用户的执行入口封装在 `tools/`，只暴露并发、速率、时长、目标、数据等必要参数。
- 🧾 **中文 HTML 报告**：解析 Locust CSV + 失败明细，输出含 TPS / RT / 错误率趋势、请求级瓶颈、调用链热图的中文报告。
- 🤖 **LLM 辅助分析（可选）**：经 `config/ai_apiclient.py`（OpenAI 兼容封装）为报告追加 AI 摘要，遵守"证据链 / 反证法 / 药方+复验"的排查纪律，密钥走环境变量。
- 🧑‍💻 **AI 对话协作（Agent-ready）**：内置「规划 → 脚本开发 → 报告分析」三 Agent 规则，Codex / Claude Code / DSH 等工具打开项目即可用自然语言驱动全流程压测。
- 📈 **阶梯加压找拐点**：`tools/run_step_load.py` 逐级加压并自动判定 TPS 性能拐点与错误率击穿点，支持熔断。
- 🔍 **瓶颈模式库**：`docs/performance_known_issues.md` 沉淀常见瓶颈（慢 SQL、连接池、Redis 热点、GC 风暴等），供定位与报告分析参考。
- 📊 **多源监控接入**：支持 Prometheus 资源 CSV + 慢查询 JSONL，在报告中叠加"资源与吞吐关联曲线"与慢查询 Top，作为交叉验证的第二层证据。
- 🩺 **执行前预检**：`tools/preflight.py` 自动检查占位 host、占位密钥与环境对齐，避免误压空白环境。
- 🧼 **敏感数据默认不入库**：`.gitignore` 默认忽略 `data/` 下的真实 txt/json/csv，仓库只保留 `data/examples/` 脱敏示例。

## 📁 目录结构

```text
.
├── AGENTS.md                 # Agent 规则入口（目录边界/命名/不覆盖/规范）
├── agents/                   # 规划 / 脚本开发 / 报告分析 Agent 职责模板
├── config/
│   ├── config.ini            # 运行配置（host、环境、等待时间、日志）
│   ├── ai_apiclient.py       # LLM 调用封装（环境变量驱动）
│   └── performance_profile.yaml
├── data/
│   └── examples/             # 脱敏示例数据（脚本默认指向）
├── docs/                     # 工作流与报告设计
├── examples/                 # 可公开的端到端压测示例（含报告成品）
├── locustfiles/              # Locust 压测脚本
├── logs/                     # 框架运行日志（不入库）
├── reports/                  # raw/ 原始产物、html/ 中文报告、analysis/ 中间产物
├── tools/                    # 命名/日志/报告生成/执行封装
└── tests/                    # 自包含单元测试（不触网、不调真实 LLM）
```

## 🚀 快速开始

### 安装

```bash
# 需要 Python >= 3.11，推荐使用 uv
uv sync --extra dev
# 或用 pip:
pip install -e .[dev]
```

### 查看公开示例

仓库 `examples/` 目录存放了**端到端跑通**的脱敏压测示例（含脚本、数据、原始
产物与中文 HTML 报告成品），适合先看产物再动手复现，也方便理解框架的标准流程
与各类测试类型：

| 示例 | 场景 / 测试类型 | 示范点 | 成品报告 |
| --- | --- | --- | --- |
| [`health-check-demo`](examples/health-check-demo/) | 健康检查 · 基准/负载 | 单步基准 + 阶梯加压找拐点 | `report.html` |
| [`pressure-test-demo`](examples/pressure-test-demo/) | 健康检查 · 压力 | 加压到服务过载，击穿点与熔断判定 | `report.html` |

```bash
# 直接打开某个示例的成品报告
open examples/health-check-demo/report.html
open examples/pressure-test-demo/report.html
```

每个示例的 `README.md` 都给出了「启动本地 Mock 被测服务 → 预检 → 压测 →
生成中文报告」的可一次性复现命令，完整清单见
[`examples/README.md`](examples/README.md)。

### 跑通一个示例场景（健康检查）

```bash
uv run locust -f locustfiles/locust_demo_health_baseline.py \
  --headless -u 1 -r 1 -t 10s
```

默认 host 从 `config/config.ini` 读取（仓库内为 `https://example.com` 占位）。可用 `LOCUST_TARGET_HOST` 覆盖指向你的被测服务：

```bash
LOCUST_TARGET_HOST=https://your-service.example.com uv run locust \
  -f locustfiles/locust_demo_health_baseline.py --headless -u 1 -r 1 -t 10s
```

### 一体化执行入口

每个场景都封装为 `uv run python -m tools.xxx ...`，自动创建时间戳目录、生成 Locust 原始产物、失败明细 JSONL 与中文 HTML 报告：

```bash
# 基准测试：健康检查
uv run python -m tools.report_builder \
  --scenario demo_health --test-type baseline \
  --stats-csv reports/raw/demo_health/<timestamp>/stats_stats.csv \
  --overview "示例：健康检查接口基准摸底" \
  --plan "1 个虚拟用户，持续 10 秒，建立响应时间基线" \
  --users 1 --spawn-rate "1 user/s" --run-time "10s" \
  --host "https://example.com" \
  --data-file "data/examples/demo_health_payloads.csv" \
  --api-name "健康检查接口" --method GET --path /health

# 阶梯加压找拐点（容量/压力测试）：
uv run python -m tools.run_step_load \
  --locustfile locustfiles/locust_demo_health_baseline.py \
  --steps "10,30,50,80,100" --step-duration 30s \
  --scenario demo_health --host https://your-service.example.com
```

所有入口都支持 `--host` / `--path` / `--data-file` / `--url-file` 覆盖默认值；如某个环境网络或 LLM 网关不可用，加 `--no-llm-analysis` 跳过 AI 摘要。

**正式压测前建议先做预检**，避免误压占位环境或带占位密钥的数据：

```bash
uv run python -m tools.preflight \
  --host https://your-service.example.com \
  --data-file data/examples/demo_health_payloads.csv
```

> 只需 Locust 原始产物、不需要二次报告时，也可以直接用底层 `locust` 命令（见各 `tools/run_*.py` 的 help）。

## 🔧 配置

| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `LOCUST_TARGET_HOST` | 被压测服务根地址（覆盖 `config.ini host`） | `https://example.com` |
| `AI_APICLIENT_BASE_URL` | LLM 网关 base_url（OpenAI 兼容） | `http://localhost:8000/v1` |
| `AI_APICLIENT_API_KEY` | LLM API Key | `not-configured` |
| `AI_APICLIENT_MODEL` | 模型名 | `demo-model` |
| `AI_APICLIENT_EXTRA_HEADERS` | 透传给网关的额外 header（JSON） | 空 |

LLM 分析名为可选增强：未配置环境不会真正调用；交叉 `--no-llm-analysis` 跳过。**请勿在仓库提交真实密钥**。

## 🤖 AI 对话协作（Codex / Claude Code / DSH）

本项目**天然面向 AI 工具（Codex、Claude Code、DeepSeek Harness 等）驱动**：
仓库内置了一套「规划 → 脚本开发 → 报告分析」的三 Agent 协作规则。当用任意 Agentic
工具打开本项目根目录时，**工具会自动读取 `AGENTS.md` 与 `agents/*.md`**，获得压测
全流程的领域规则（命名规范、目录边界、防覆盖、指标口径、熔断红线、报告纪律），从而
可以用自然语言直接驱动一次完整的压测。

### 三 Agent 协作工作流

| Agent | 指令文件 | 职责 | 输入 → 输出 |
| --- | --- | --- | --- |
| **规划 Agent** | [`agents/planning_agent.md`](agents/planning_agent.md) | 确认业务背景、接口信息、测试类型、并发模型、指标口径、熔断/退出条件 | 对话信息 → 结构化压测方案（YAML） |
| **脚本开发 Agent** | [`agents/script_development_agent.md`](agents/script_development_agent.md) | 按方案生成 Locust 脚本、数据文件、运行命令 | 压测方案 → `locustfiles/` + `data/` 产物 |
| **报告分析 Agent** | [`agents/report_analysis_agent.md`](agents/report_analysis_agent.md) | 多指标证据链归因，输出带优先级与复测方案的中文报告 | 原始产物 → `reports/html/` 决策级报告 |

> 🪄 想直接开聊？[`docs/agent_prompts.md`](docs/agent_prompts.md) 提供**一键全流程 / 分步推进 / 进阶精调**三档可直接复制的提示词模板，填上你的接口信息即可。

### 典型对话流程

**第一步，告诉规划 Agent 你的目标**（工具会按 `planning_agent.md` 逐项与你确认）：

> 「帮我规划一个上线前单接口基准测试：`POST /api/v1/orders`，预期日活 10000，
> 目标定位最慢接口和错误率。」

规划 Agent 会依次确认：业务背景、接口/认证/请求体、测试目标→策略、并发模型、
环境对齐、数据来源、算力估算、SLA 阈值与熔断红线，最后输出结构化方案。

**第二步，交给脚本开发 Agent**：

> 「按刚才的方案开发 Locust 脚本和数据文件。」

脚本开发 Agent 会生成符合 `locust_<业务域>_<接口>_<测试类型>.py` 命名的脚本、
脱敏数据文件与一体化运行命令。

**第三步，交给报告分析 Agent**：

> 「分析 `reports/raw/orders/20260910_120000/` 并生成中文报告。」

报告分析 Agent 读取原始 CSV/失败明细，遵循「多指标证据链 + 反证法 + 药方&复测」
纪律，产出可决策的中文 HTML 报告。

### 各 AI 工具的使用方式

- **Codex**：在项目根目录打开会话，直接描述压测诉求，让它在 `AGENTS.md` 约束下
  完成规划→脚本→执行→报告的全流程。
- **Claude Code**：同样进入项目根目录，工具会加载 `CLAUDE.md` / `AGENTS.md` /
  `agents/*.md`，可用自然语言分阶段推进。
- **DSH / 其它 Agent 工具**：让 Agent 先读取上述规则文件，再开始对话协作。

### 程序化加载 Agent 指令

如需在代码或编排层复用这三段指令（例如未来接入
`openai-agent-framework-py`），可调用 `agents/performance_agents.py`：

```python
from agents.performance_agents import load_agent_instruction

planning   = load_agent_instruction("planning")           # 规划 Agent 指令
dev        = load_agent_instruction("script_development") # 脚本开发 Agent 指令
analysis   = load_agent_instruction("report_analysis")    # 报告分析 Agent 指令
```

> 该项目基于 Codex、Claude Code、DSH 等 Agentic 工具打开即可获得完整对话协作能力，
> 不需要额外配置；工具会自动把上述 Markdown 规则作为 Agent 的领域上下文注入。

## 🧪 测试

测试自包含：不触网、不调真实 LLM（使用 Fake 响应）：

```bash
uv run pytest
uv run ruff check .
```

## 🤝 参与贡献

欢迎提交 PR。请遵守：

1. 遵循 `AGENTS.md` 的命名、边界与「不覆盖报告」规则。
2. **不提交真实业务数据与密钥**；新数据请放 `data/examples/` 或本地生成。
3. 新功能/脚本请附带 `tests/` 自包含用例并保证 `ruff check` 通过。

## 📄 License

[MIT](LICENSE)