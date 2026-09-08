#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""命名工具测试。"""

from __future__ import annotations

from pathlib import Path

from tools.naming import build_report_paths, ensure_unique_path


def test_ensure_unique_path_appends_index(tmp_path: Path) -> None:
    """已存在文件不会被覆盖。"""

    target = tmp_path / "report.html"
    target.write_text("old", encoding="utf-8")

    unique = ensure_unique_path(target)

    assert unique == tmp_path / "report_01.html"


def test_build_report_paths_uses_timestamp(tmp_path: Path) -> None:
    """报告路径必须包含场景、类型和时间戳。"""

    paths = build_report_paths("demo", "baseline", tmp_path, "20260623_220000")

    assert paths.raw_dir == tmp_path / "reports" / "raw" / "demo" / "20260623_220000"
    assert paths.html_report == tmp_path / "reports" / "html" / "demo_baseline_20260623_220000.html"
