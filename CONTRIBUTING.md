# 参与贡献指南

感谢你愿意为 **test-locust-platform** 贡献代码。请先阅读 [AGENTS.md](AGENTS.md)
中的命名、目录边界、不覆盖与开发规范。本框架的准则是：**框架不承载具体业务规则，
脚本只描述行为，数据只从 data 读取，报告必须给出可决策的结论。**

## 提交什么类型的 PR

- 修复 bug、完善文档、补充测试。
- 新增一个「可复用」的压测入口（脚本 + data 示例 + 入口 + 测试）。
- 报告生成器 / 命名工具 / 日志工具等框架能力的增强。

## 不要提交

- **真实业务数据**（生产域名、账号、流水、密钥等）— 一律放 `data/examples/` 或本地。
- 任何 `.ini` / `.yaml` / 代码中**硬编码的内部 host / token / appKey**。
- 覆盖或删除历史 `reports/`、`logs/` 产物（沿用时间戳 + 序号命名，禁止覆盖）。

## 提交流程

1. 遵守 AGENTS.md 的命名与分层约定。
2. 新功能附带自包含 `tests/` 用例（使用 Fake，不触网、不调真实 LLM）。
3. 本地跑通：
   ```bash
   uv run ruff check .
   uv run pytest -q
   ```
4. 提交 PR，CI 会自动跑 lint + 多版本 pytest。

## 许可证

本项目采用 [MIT License](LICENSE)。提交即视为同意你的贡献在 MIT 下发布。