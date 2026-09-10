# AI Agent 入门提示词模板（Codex / Claude Code / DSH）

本项目是 **Agent-ready** 的：仓库内的 `AGENTS.md`、`agents/*.md` 定义了一套
「规划 → 脚本开发 → 报告分析」的三 Agent 协作规则。使用 Codex、Claude Code、
DSH 等 Agentic 工具打开项目根目录后，工具会自动加载这些规则作为领域上下文。

本文件给出**可直接复制**的提示词模板，按使用场景分三档：**一键全流程**、
**分步推进**、**进阶精调**。请把你的真实信息填入 `[方括号]` 占位处。

---

## 一、一键全流程提示词（推荐新手）

把下面整段复制给 Agent 即可自动完成「规划 → 脚本 → 执行（可选）→ 报告」：

```text
你是性能测试专家。在这个 locust-perf-framework 项目里，请按 AGENTS.md 与
agents/*.md 的三 Agent 协作流程，帮我完成一次完整的压测，输出结构化方案、
脚本与中文报告。

【业务背景】[例如：电商订单创建接口上线前摸底]
【被测接口】[例如：POST /api/v1/orders，请求体 {orderId, items[]...}]
【测试目的】[基准 / 负载 / 压力 / 稳定性 / 峰值，选一或多个]
【预期流量】[例如：日活 10000，高峰期每秒 200 请求]
【目标环境】[例如：staging，与生产配置对齐]
【数据说明】[例如：只读接口，无敏感字段 / 写接口，需构造幂等数据]
【SLA 阈值】[例如：P95 < 300ms，错误率 < 1%]
【其他要求】[例如：加 --no-llm-analysis / 需要历史对比]

请先按规划 Agent 的确认顺序逐项向我确认缺失的信息，再输出最终方案。
```

工具会自动读取 `AGENTS.md` 与 `agents/*.md`，并按 `planning_agent.md` 的
对话顺序和你确认细节。

---

## 二、分步推进提示词

如果希望人工把控每一步，可拆成三段依次使用。

### 第 1 步 · 规划 Agent

```text
按 agents/planning_agent.md 的流程，向我逐项确认以下信息，最后输出一份
结构化的压测方案（YAML，含 scenario / load_model / metrics / thresholds /
abort_conditions / risks）：
- 业务背景与压测目的
- 被压测接口、请求方法、认证、请求头/体、参数化
- 测试目标类型（baseline|load|capacity|stability|spike）
- 并发模型（用户数、spawn_rate、时长、加压方式）
- 环境对齐情况
- 数据来源与敏感信息处理
- 算力估算（所需节点与每节点并发）
- 指标口径、SLA、熔断与停止条件
```

### 第 2 步 · 脚本开发 Agent

```text
按 agents/script_development_agent.md 与上面确认的方案，开发压测脚本和数据：
- 脚本放 locustfiles/，命名 locust_<业务域>_<接口>_<测试类型>.py
- 数据放 data/ 或 data/examples/（脱敏示例），脚本不写死真实数据
- task 中注明接口名、请求方法、校验点、失败记录
- 提供一体化运行命令（含 --headless -u -r -t）
```

### 第 3 步 · 报告分析 Agent

```text
按 agents/report_analysis_agent.md 分析 reports/raw/<场景>/<时间戳>/ 下的原始
产物，生成中文 HTML 报告：
- 输出到 reports/html/，命名 <场景>_<测试类型>_<时间戳>.html
- 优先看 P95/P99 分位，不看平均值判好坏
- 用多指标证据链 + 反证法归因瓶颈，别单看一张图下结论
- 每条优化建议给出优先级、预期收益与复测验证方法
```

---

## 三、进阶精调提示词

### 指定测试类型口径

```text
这是 capacity/压力测试。请遵守：逐步阶梯加压找 TPS 拐点（120% 超压），
禁一次性打满；熔断红线为错误率>5% 或 RT 超 SLA 3 倍时自动暂停。
阶梯建议 [10,30,50,80,100]，每级 [30s]。
```

### 稳定性测试口径

```text
这是稳定性测试。禁止满载，用低/中并发混合长时间运行 [2h]，重点看内存趋势、
错误率累积、TPS 波动；请在报告中给出内存/资源趋势分析。
```

### 峰值/突发测试口径

```text
这是峰值测试。用脉冲/浪涌加压模拟秒杀突发，关注突发 TPS 峰值与恢复时间，
不要做阶梯式稳定加压。
```

### 报告分析纪律（交付前强制）

```text
生成报告时严格执行：结论必须有数据支撑；区分「必须修」与「建议优化」；
每条建议给优先级+预期收益+用 --same-as 同压力复测的验证方式；结论要回答
「瓶颈在哪、证据是什么、怎么改、改完怎么验证」，不能只堆指标。
```

---

## 四、程序化接入（可选）

如需在代码或编排层复用这三段 Agent 指令（如接入 `openai-agent-framework-py`）：

```python
from agents.performance_agents import load_agent_instruction

planning = load_agent_instruction("planning")
dev      = load_agent_instruction("script_development")
analysis = load_agent_instruction("report_analysis")
```

---

> 提示：真实压测请先运行 `uv run python -m tools.preflight` 做执行前预检，
> 避免误压占位环境或带占位密钥的数据。所有环境变量/密钥通过 `.env` 或环境注入，
> 不要在仓库与提示词中提交真实密钥。