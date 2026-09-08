#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""payOutReturn 一体化执行入口测试。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from tools.run_pay_out_return_stress import _build_context, _build_html_report, _run_locust


def test_run_locust_sets_env_and_command(monkeypatch, tmp_path: Path) -> None:
    """执行入口会自动拼装 Locust 命令和环境变量。"""

    captured: dict[str, object] = {}

    def fake_run(command, check, env):  # noqa: ANN001
        captured["command"] = command
        captured["check"] = check
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    args = argparse.Namespace(
        users=2,
        spawn_rate=2,
        run_time="1m",
        host="https://example.com",
        path="/op-manage-inner-api/op/assets/payOutReturn",
        data_file="data/examples/pay_out_return_stress.json",
    )

    return_code = _run_locust(tmp_path, tmp_path / "failure_details.jsonl", args)

    assert return_code == 0
    assert captured["check"] is False
    assert captured["command"][0] == sys.executable
    assert "locustfiles/locust_op_pay_out_return_stress.py" in captured["command"]
    assert captured["env"]["LOCUST_PAY_OUT_RETURN_HOST"] == "https://example.com"
    assert captured["env"]["LOCUST_PAY_OUT_RETURN_FAILURE_DETAILS_FILE"].endswith(
        "failure_details.jsonl"
    )


def test_build_context_contains_business_fields() -> None:
    """中文报告上下文包含接口业务信息。"""

    args = argparse.Namespace(
        users=2,
        spawn_rate=2,
        run_time="1m",
        host="https://example.com",
        path="/op-manage-inner-api/op/assets/payOutReturn",
        data_file="data/examples/pay_out_return_stress.json",
        scenario="op_pay_out_return",
        test_type="stress",
        environment="test",
        executor="QA",
    )

    context = _build_context(args)

    assert context.api_name == "payOutReturn 支付回调接口"
    assert context.method == "POST"
    assert context.path == "/op-manage-inner-api/op/assets/payOutReturn"
    assert "1001" in context.overview
    assert "1002" in context.overview


def test_build_html_report_enables_llm_by_default(monkeypatch, tmp_path: Path) -> None:
    """一体化入口默认启用 LLM 辅助分析。"""

    captured: dict[str, object] = {}

    def fake_build_report(*args, **kwargs):  # noqa: ANN002, ANN003
        captured.update(kwargs)
        return tmp_path / "report.html"

    monkeypatch.setattr("tools.run_pay_out_return_stress.build_report", fake_build_report)
    args = argparse.Namespace(
        users=2,
        spawn_rate=2,
        run_time="1m",
        host="https://example.com",
        path="/op-manage-inner-api/op/assets/payOutReturn",
        data_file="data/examples/pay_out_return_stress.json",
        scenario="op_pay_out_return",
        test_type="stress",
        environment="test",
        executor="QA",
        baseline_stats_csv=None,
        no_llm_analysis=False,
    )

    report = _build_html_report(
        tmp_path,
        tmp_path / "failure_details.jsonl",
        tmp_path / "stats_stats.csv",
        "20260827_160000",
        args,
    )

    assert report == tmp_path / "report.html"
    assert captured["enable_llm_analysis"] is True


def test_build_html_report_can_disable_llm(monkeypatch, tmp_path: Path) -> None:
    """网络或网关不可用时可显式跳过 LLM 辅助分析。"""

    captured: dict[str, object] = {}

    def fake_build_report(*args, **kwargs):  # noqa: ANN002, ANN003
        captured.update(kwargs)
        return tmp_path / "report.html"

    monkeypatch.setattr("tools.run_pay_out_return_stress.build_report", fake_build_report)
    args = argparse.Namespace(
        users=2,
        spawn_rate=2,
        run_time="1m",
        host="https://example.com",
        path="/op-manage-inner-api/op/assets/payOutReturn",
        data_file="data/examples/pay_out_return_stress.json",
        scenario="op_pay_out_return",
        test_type="stress",
        environment="test",
        executor="QA",
        baseline_stats_csv=None,
        no_llm_analysis=True,
    )

    _build_html_report(
        tmp_path,
        tmp_path / "failure_details.jsonl",
        tmp_path / "stats_stats.csv",
        "20260827_160000",
        args,
    )

    assert captured["enable_llm_analysis"] is False
