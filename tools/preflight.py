#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""压测执行前预检（Preflight）工具。

在执行压测前做一组"低成本、高价值"的校验，避免常见错误：
- 目标 host 仍是占位符（example.com 等）时给出强提示；
- 数据文件是否包含疑似密钥/占位 appKey；
- 环境对齐提醒（压测环境应对齐基准/生产，否则结论失真）。

真实压测前建议调用本工具，也可接入各 run_* 入口。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER_HOST_MARKERS = ("example.com", "localhost:8000", "localhost:3000")
SENSITIVE_PATTERNS = {
    "appKey 占位": re.compile(r"<your[-_ ]?(app[-_ ]?key|key)[^>]*>", re.IGNORECASE),
    "疑似 appKey": re.compile(r'"appKey"\s*:\s*"[0-9a-f]{16,}"', re.IGNORECASE),
    "疑似 token": re.compile(r'"([a-z_-]*token[a-z_-]*)"\s*:\s*"[^"]{8,}"', re.IGNORECASE),
    "疑似密钥": re.compile(r'"api[-_ ]?(key|secret)"\s*:\s*"[^"]{8,}"', re.IGNORECASE),
}


@dataclass(frozen=True)
class PreflightIssue:
    """一条预检发现。"""

    severity: str  # error | warn | info
    message: str


def check_host(host: str) -> PreflightIssue | None:
    """检查 host 是否仍是占位符，避免误打空白环境。"""

    if any(marker in host.lower() for marker in PLACEHOLDER_HOST_MARKERS):
        return PreflightIssue(
            severity="error",
            message=(
                f"目标 host 仍是占位符：{host}。请用 --host / LOCUST_TARGET_HOST "
                "指向可压测的被测服务，或用 --no-preflight 跳过本项。"
            ),
        )
    return None


def check_data_file(data_file: str) -> list[PreflightIssue]:
    """检查数据文件是否含疑似占位密钥或真实密钥。"""

    issues: list[PreflightIssue] = []
    path = Path(data_file)
    if not data_file or not path.exists():
        return issues
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return issues
    for label, pattern in SENSITIVE_PATTERNS.items():
        if pattern.search(content):
            issues.append(
                PreflightIssue(
                    severity="warn",
                    message=f"数据文件 {data_file} 中检测到 {label}，请确认实际应用到被测服务前已替换为真实值。",
                )
            )
    return issues


def check_environment_alignment() -> PreflightIssue:
    """环境对齐提醒（属于信息级）。"""

    return PreflightIssue(
        severity="info",
        message="请确认压测环境与基准/生产配置对齐（数据库、连接池、缓存大小），并建议先跑基准测试。",
    )


def run_preflight(*, host: str = "", data_file: str = "") -> list[PreflightIssue]:
    """执行完整预检：返回全部问题（空列表 = 通过）。"""

    issues: list[PreflightIssue] = []
    if host:
        host_issue = check_host(host)
        if host_issue:
            issues.append(host_issue)
    issues.extend(check_data_file(data_file))
    issues.append(check_environment_alignment())
    return issues


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(description="Run preflight checks before a load test.")
    parser.add_argument("--host", default="", help="被测目标 host")
    parser.add_argument("--data-file", default="", help="数据文件路径")
    args = parser.parse_args()

    issues = run_preflight(host=args.host, data_file=args.data_file)
    if not issues:
        print("preflight: 通过，可执行压测。")
        return

    has_error = False
    for issue in issues:
        prefix = {"error": "[ERROR]", "warn": "[WARN ]", "info": "[INFO ]"}[issue.severity]
        print(f"{prefix} {issue.message}")
        if issue.severity == "error":
            has_error = True

    if has_error:
        print("\n存在 ERROR 级问题，请修复后再执行压测。")
        raise SystemExit(2)
    print("\n预检完成（未阻断）。可执行压测，但请关注 WARN/INFO 项。")


if __name__ == "__main__":
    main()