# 安全说明

本项目是一个通用的性能测试框架，本身不采集用户数据。为保证仓库与使用者的安全，
请注意以下几点：

## 报告安全问题

如果发现漏洞或安全隐患（例如数据脱敏失效、密钥可能泄露、命令注入等），请**不要
公开 Issue**，改为私有渠道联系维护者，或使用 GitHub 的
[Security Advisory](https://docs.github.com/en/code-security/security-advisories)
功能报告。

## 给使用者的安全提醒

- **不要向仓库提交真实业务数据、生产域名、账号或任何密钥**。数据请放在
  `data/examples/` 的脱敏示例或仓库外的本地文件。
- **不要在代码 / 配置中硬编码 host、appKey、token、cookie**。请用
  `config/config.ini` + 环境变量（见 `.env.example`）注入。
- 执行压测前，确认目标 host 是**你有权测试**的服务，遵守被测系统所属公司的
  测试规范与合规要求。
- LLM 分析功能是可选项，密钥走 `AI_APICLIENT_API_KEY` 环境变量，切勿写进代码。