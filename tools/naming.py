#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""命名与不覆盖保护工具。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def timestamp() -> str:
    """返回报告和目录使用的时间戳。"""

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_unique_path(path: Path) -> Path:
    """返回一个不存在的路径，避免覆盖历史产物。"""

    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(1, 1000):
        candidate = parent / f"{stem}_{index:02d}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"无法生成唯一文件名: {path}")


@dataclass(frozen=True)
class ReportPaths:
    """一次压测执行对应的报告路径。"""

    raw_dir: Path
    html_report: Path
    analysis_markdown: Path


def build_report_paths(
    scenario: str,
    test_type: str,
    base_dir: Path | str = ".",
    current_timestamp: str | None = None,
) -> ReportPaths:
    """构造原始报告、HTML 报告和分析文件路径。"""

    root = Path(base_dir)
    ts = current_timestamp or timestamp()
    raw_dir = root / "reports" / "raw" / scenario / ts
    html_report = root / "reports" / "html" / f"{scenario}_{test_type}_{ts}.html"
    analysis_markdown = root / "reports" / "analysis" / f"{scenario}_{test_type}_{ts}.md"
    return ReportPaths(
        raw_dir=ensure_unique_path(raw_dir),
        html_report=ensure_unique_path(html_report),
        analysis_markdown=ensure_unique_path(analysis_markdown),
    )
