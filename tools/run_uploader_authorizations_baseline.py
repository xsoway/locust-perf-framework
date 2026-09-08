#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时密钥接口压测与中文报告一体化执行入口。"""

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
DEFAULT_PATH = "/file-center-api/open/bucket/uploaderAuthorizations"
DEFAULT_DATA_FILE = "data/examples/uploader_authorizations_baseline.json"


def _timestamp() -> str:
    """返回本次执行批次时间戳。"""

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_locust(raw_dir: Path, failure_details_file: Path, args: argparse.Namespace) -> int:
    """执行临时密钥接口 Locust 压测。"""

    env = os.environ.copy()
    env["LOCUST_UPLOADER_AUTHORIZATIONS_HOST"] = args.host
    env["LOCUST_UPLOADER_AUTHORIZATIONS_PATH"] = args.path
    env["LOCUST_UPLOADER_AUTHORIZATIONS_DATA_FILE"] = args.data_file
    env["LOCUST_UPLOADER_AUTHORIZATIONS_FAILURE_DETAILS_FILE"] = str(failure_details_file)

    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "locustfiles/locust_file_center_uploader_authorizations_baseline.py",
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
    """构造临时密钥接口中文报告上下文。"""

    return ReportContext(
        scenario=args.scenario,
        test_type=args.test_type,
        overview="针对文件中心临时密钥接口进行基准压测，验证上传授权获取链路的响应时间、失败率和稳定性。",
        plan=(
            f"以 {args.users} 并发、每秒 {args.spawn_rate} 用户爬坡执行，"
            f"持续 {args.run_time}；请求头和请求体统一从 {args.data_file} 读取。"
        ),
        environment=args.environment,
        executor=args.executor,
        users=str(args.users),
        spawn_rate=f"{args.spawn_rate} user/s",
        run_time=args.run_time,
        host=args.host,
        data_file=args.data_file,
        api_name="uploaderAuthorizations 临时密钥接口",
        method="POST",
        path=args.path,
        success_criteria="HTTP 200，且 JSON 响应未出现 success=false 或非 0/200 业务 code。",
        extra_note="appKey 仅用于接口调用，脚本日志和失败明细会对该请求头脱敏。",
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
        description="Run uploaderAuthorizations baseline test and build CN report."
    )
    parser.add_argument("--users", type=int, default=10, help="并发用户数")
    parser.add_argument("--spawn-rate", type=float, default=2, help="每秒启动用户数")
    parser.add_argument("--run-time", default="1m", help="压测持续时间，例如 1m、10m")
    parser.add_argument("--host", default=DEFAULT_HOST, help="目标 host")
    parser.add_argument("--path", default=DEFAULT_PATH, help="接口路径")
    parser.add_argument("--data-file", default=DEFAULT_DATA_FILE, help="请求数据文件")
    parser.add_argument("--scenario", default="uploader_authorizations", help="场景名")
    parser.add_argument("--test-type", default="baseline", help="测试类型")
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
