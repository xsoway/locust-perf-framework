# CLAUDE.md - project parent-rule bridge

<!-- code_project: parent-agents-required -->

Claude Code 打开本项目时，开始任何任务前必须依次读取并遵循：`../../AGENTS.md`、`../AGENTS.md`、`AGENTS.md`、本文件。规则优先级固定为：用户当前明确要求 > 工作区根 `AGENTS.md` > `src/AGENTS.md` > 项目局部规则；项目规则只能补充，不能放宽父级规则。

