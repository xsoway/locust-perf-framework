# 报告分析 Agent

## 目标

读取 Locust 原始 CSV、日志和执行上下文，生成可决策的 HTML 报告。

## 输入

- `reports/raw/<场景名>/<时间戳>/`
- `*_stats.csv`
- `*_failures.csv`
- `*_exceptions.csv`
- 执行日志和压测方案

## 输出

- `reports/html/<场景名>_<测试类型>_<时间戳>.html`
- `reports/analysis/<场景名>_<测试类型>_<时间戳>.md`

## LLM 使用

调用 `config/ai_apiclient.py` 时，只用于辅助归纳，不替代原始指标证据。
