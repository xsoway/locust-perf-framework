---
name: Bug 报告
about: 报告框架或脚本的缺陷
title: "[Bug] "
labels: bug
assignees: ''
---

## 描述

一句话说明 bug 的现象。

## 复现步骤

1. 使用的命令 / 入口（例如 `uv run python -m tools.report_builder ...`）
2. 实际现象（报错堆栈 / 不符合预期的输出）

## 期望行为

本应发生什么。

## 环境

- 版本 / 分支：`master @ <commit>`（`git rev-parse HEAD`）
- Python 版本：`uv run python --version`
- 操作系统：
- 是否触网 / 是否调用了真实 LLM：否 / 是

## 日志与产物

粘贴关键日志（隐藏密钥），或补充 `reports/`、`logs/` 里的相关文件路径（**不要提交敏感数据**）。

## 排查提示（可选）

是否已定位到疑似模块（如 `tools/report_builder.py`、`tools/run_step_load.py`）或相关 `tests/` 用例？