# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [v0.1.0] - 2026-09-10

首个公开版本，作为「通用性能测试框架」开源发布。

### 🎉 亮点

- 基于 Python + Locust 的通用压测框架，沉淀「方案 → 脚本 → 数据 → 执行记录 → 中文报告 → LLM 分析」可复用流程。
- **Agent-ready**：内置「规划 → 脚本开发 → 报告分析」三 Agent 协作规则，Codex / Claude Code / DSH 等工具打开即用。

### ✨ 新增（框架能力）

- 一体化命令封装：`tools/` 下 `run_step_load`（阶梯加压找拐点/击穿，支持熔断）、`report_builder`（中文 HTML 报告）、`preflight`（执行前预检）。
- 多源监控接入：报告叠加 Prometheus 资源 CSV + 慢查询 JSONL，形成交叉验证的第二层证据（`a1d1037`）。
- AI 对话协作：三 Agent 规则（`agents/*.md`）+ 程序化入口 `agents/performance_agents.py::load_agent_instruction`。
- 提示词模板库：`docs/agent_prompts.md`（一键全流程 / 分步 / 进阶精调）。

### 🧪 示例

- 新增 `examples/health-check-demo/`：健康检查基准 + 阶梯加压找拐点，含成品报告。
- 新增 `examples/pressure-test-demo/`：压力测试加压到服务过载，演示击穿点(并发40)与熔断判定，含成品报告。
- 每个示例均提供可一次性复现命令、脱敏数据、原始产物与中文 HTML 报告。

### 🐛 修复

- `report_builder.parse_failure_details` 对非 JSONL 失败明细文件优雅降级，不再中断整份报告生成（`13f0a50`）。
- 预检数据改为合成 appKey 样本，避免真实密钥残留仓库（`328f655`）。

### ♻️ 重构 / 清理

- 项目通用化并更名为 `locust-perf-framework`，适配开源发布（`45dc307`）。
- 精简为单一健康检查示例场景，移除 4 套历史业务压测脚本（`d17728f`）。
- 清理历史数据、日志、报告产物与缓存；修正删数据后残留的路径引用（`87a10e4`）。

### 📚 文档

- 主 README 新增「AI 对话协作」「查看公开示例」章节。
- 新增 `docs/agent_prompts.md`、`examples/README.md` 及各示例 README。

### ✅ 质量

- 全量单元测试 37 个自包含通过（不触网、不调真实 LLM）。
- `ruff check` 通过。

### 安全

- `.gitignore` 精准豁免 `examples/**`；真实 `reports/`、`logs/` 产物保持不入库。
- 所有示例与代码仅含 `example.*` / `127.0.0.1` 占位，无真实业务数据与密钥。