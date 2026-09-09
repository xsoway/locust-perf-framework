#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""压测执行前预检工具测试。"""

from __future__ import annotations

from pathlib import Path

from tools.preflight import check_data_file, check_host, run_preflight


def test_check_host_flags_placeholder() -> None:
    """example.com 等占位 host 应被标记为 error。"""

    issue = check_host("https://example.com")
    assert issue is not None
    assert issue.severity == "error"


def test_check_host_allows_real_host() -> None:
    """真实 host 不应被标记。"""

    assert check_host("https://perf.internal.example.org") is None


def test_check_data_file_detects_placeholder_appkey(tmp_path: Path) -> None:
    """数据文件中的占位 appKey 应被 warning。"""

    data_file = tmp_path / "case.json"
    data_file.write_text('{"headers": {"appKey": "<your-app-key-here>"}}', encoding="utf-8")

    issues = check_data_file(str(data_file))

    assert any("appKey" in i.message for i in issues)
    assert all(i.severity == "warn" for i in issues)


def test_check_data_file_detects_real_hex_appkey(tmp_path: Path) -> None:
    """数据文件中的 16 位以上十六进制 appKey 应被 warning。"""

    data_file = tmp_path / "real.json"
    data_file.write_text('{"headers": {"appKey": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"}}', encoding="utf-8")

    issues = check_data_file(str(data_file))

    assert any("完 key" in i.message or "appKey" in i.message for i in issues)


def test_run_preflight_returns_environment_info() -> None:
    """环境对齐提醒属于信息级，总会返回一条。"""

    issues = run_preflight(host="", data_file="")

    assert any(i.severity == "info" for i in issues)