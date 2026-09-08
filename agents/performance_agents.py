#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""性能测试三类 Agent 的最小入口。

后续如果接入 `openai-agent-framework-py`，可把本文件中的 prompt 作为 Agent
instructions，并通过该框架的 runtime 统一调度。
"""

from __future__ import annotations

from pathlib import Path


def load_agent_instruction(agent_name: str) -> str:
    """读取指定 Agent 的 Markdown 指令。"""

    mapping = {
        "planning": "planning_agent.md",
        "script_development": "script_development_agent.md",
        "report_analysis": "report_analysis_agent.md",
    }
    if agent_name not in mapping:
        raise ValueError(f"未知 Agent: {agent_name}")

    instruction_file = Path(__file__).with_name(mapping[agent_name])
    return instruction_file.read_text(encoding="utf-8")
