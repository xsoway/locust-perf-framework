#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time     : 2026/08/27 16:00
# @Filename : run_pay_out_return_stress.py
# @Author   : Alan_Hsu
"""payOutReturn 支付回调压力测试与中文报告一体化执行入口。

典型用法（用户A 发 requestId=1001，用户B 发 requestId=1002，
两用户同时进行、各执行一次后自动停止）：

    uv run python -m tools.run_pay_out_return_stress \\
        --users 2 --spawn-rate 2 --run-time 1m

    --users 2 对应两个用户：用户A 只发第一条请求，用户B 只发第二条请求，
    同时进行、各一次即结束。

    如需临时切换地址或数据文件：
    uv run python -m tools.run_pay_out_return_stress \\
        --users 2 --spawn-rate 2 --run-time 1m \\
        --host https://example.com \\
        --path /op-manage-inner-api/op/assets/payOutReturn \\
        --data-file data/examples/pay_out_return_stress.json

    跳过 LLM 分析：
    uv run python -m tools.run_pay_out_return_stress \\
        --users 2 --spawn-rate 2 --run-time 1m --no-llm-analysis
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.naming import ensure_unique_path
from tools.report_builder import ReportContext, build_report

DEFAULT_HOST = "https://example.com"
DEFAULT_PATH = "/op-manage-inner-api/op/assets/payOutReturn"
DEFAULT_DATA_FILE = "data/examples/pay_out_return_stress.json"


def _timestamp() -> str:
    """返回本次执行批次时间戳。"""

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_locust(raw_dir: Path, failure_details_file: Path, args: argparse.Namespace) -> int:
    """执行 payOutReturn Locust 压测。"""

    env = os.environ.copy()
    env["LOCUST_PAY_OUT_RETURN_HOST"] = args.host
    env["LOCUST_PAY_OUT_RETURN_PATH"] = args.path
    env["LOCUST_PAY_OUT_RETURN_DATA_FILE"] = args.data_file
    env["LOCUST_PAY_OUT_RETURN_FAILURE_DETAILS_FILE"] = str(failure_details_file)

    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "locustfiles/locust_op_pay_out_return_stress.py",
        "--headless",
        "-u",
        str(args.users),
        "-r",
        str(args.spawn_rate),
        "-t",
        args.run_time,
        "--csv",
        str(raw_dir / "stats"),
        "--html",
        str(raw_dir / "report.html"),
    ]

    return subprocess.run(command, check=False, env=env).returncode


def _build_context(args: argparse.Namespace) -> ReportContext:
    """构造 payOutReturn 中文报告上下文。"""

    return ReportContext(
        scenario=args.scenario,
        test_type=args.test_type,
        overview=(
            "针对运营后台 payOutReturn 支付回调接口进行压力测试：模拟两个用户"
            "并发一次，requestId 1001（示例用户A）与 requestId 1002（示例用户B）"
            "同时请求，验证并发退款回调场景下接口的响应时间、失败率和业务处理结果。"
        ),
        plan=(
            f"以 {args.users} 并发、每秒 {args.spawn_rate} 用户爬坡执行，"
            f"持续 {args.run_time}；每个用户会话内依次发送两条请求，"
            f"覆盖 requestId 1001 与 1002，请求体从 {args.data_file} 读取。"
        ),
        environment=args.environment,
        executor=args.executor,
        users=str(args.users),
        spawn_rate=f"{args.spawn_rate} user/s",
        run_time=args.run_time,
        host=args.host,
        data_file=args.data_file,
        api_name="payOutReturn 支付回调接口",
        method="POST",
        path=args.path,
        success_criteria="HTTP 200，且 JSON 响应未出现 success=false 或非 0/200 业务 code。",
        extra_note=(
            "每个会话按顺序发送两条请求，覆盖两笔独立退款回调；"
            "两条请求共用同一会员号，用于验证同一会员并发回调的幂等与一致性。"
        ),
    )


def _build_html_report(
    raw_dir: Path,
    failure_details_file: Path,
    stats_csv: Path,
    current_timestamp: str,
    args: argparse.Namespace,
) -> Path:
    """根据 Locust 原始产物生成中文 HTML 报告。"""

    return build_report(
        _build_context(args),
        stats_csv,
        current_timestamp=current_timestamp,
        failure_details_file=failure_details_file,
        history_file=raw_dir / "stats_stats_history.csv",
        baseline_stats_csv=args.baseline_stats_csv,
        enable_llm_analysis=not args.no_llm_analysis,
    )


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(
        description="Run payOutReturn stress test and build CN report."
    )
    parser.add_argument("--users", type=int, default=2, help="并发用户数（需求场景默认 2）")
    parser.add_argument("--spawn-rate", type=float, default=2, help="每秒启动用户数")
    parser.add_argument("--run-time", default="1m", help="压测持续时间，例如 1m、10m")
    parser.add_argument("--host", default=DEFAULT_HOST, help="目标 host")
    parser.add_argument("--path", default=DEFAULT_PATH, help="接口路径")
    parser.add_argument("--data-file", default=DEFAULT_DATA_FILE, help="请求数据文件")
    parser.add_argument("--scenario", default="op_pay_out_return", help="场景名")
    parser.add_argument("--test-type", default="stress", help="测试类型")
    parser.add_argument("--environment", default="test", help="测试环境")
    parser.add_argument("--executor", default="QA", help="执行人")
    parser.add_argument("--baseline-stats-csv", default=None, type=Path, help="历史基线 stats CSV")
    parser.add_argument("--no-llm-analysis", action="store_true", help="跳过 LLM 辅助分析")
    args = parser.parse_args()

    ts = _timestamp()
    raw_dir = ensure_unique_path(Path("reports") / "raw" / args.scenario / ts)
    raw_dir.mkdir(parents=True, exist_ok=True)
    failure_details_file = raw_dir / "failure_details.jsonl"

    locust_return_code = _run_locust(raw_dir, failure_details_file, args)

    stats_csv = raw_dir / "stats_stats.csv"
    if not stats_csv.exists():
        raise RuntimeError(
            f"Locust 执行失败且未生成 stats CSV，退出码={locust_return_code}，"
            f"请查看原始产物目录: {raw_dir}"
        )

    html_report = _build_html_report(raw_dir, failure_details_file, stats_csv, ts, args)

    if locust_return_code != 0:
        print(
            f"Locust 退出码为 {locust_return_code}，通常表示本次压测存在失败请求；"
            "已继续生成中文报告，请以报告中的失败率和失败明细判断结果。"
        )
    print(f"Locust 原始产物目录: {raw_dir}")
    print(f"失败明细 JSONL: {failure_details_file}")
    print(f"中文 HTML 报告: {html_report}")


if __name__ == "__main__":
    main()
