#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图片 URL 压测与中文报告一体化执行入口。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from tools.naming import ensure_unique_path
from tools.report_builder import ReportContext, build_report


def _timestamp() -> str:
    """返回本次执行批次时间戳。"""

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_summary(summary_file: Path) -> dict[str, object]:
    """读取图片压测辅助统计。"""

    if not summary_file.exists():
        return {}
    return json.loads(summary_file.read_text(encoding="utf-8"))


def _read_first_url(url_file: str) -> str:
    """读取 URL 数据文件中的首条有效 URL。"""

    path = Path(url_file)
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            return value
    return ""


def _derive_url_scope(url_file: str) -> tuple[str, str]:
    """从 URL 文件推导报告中的目标域名和请求路径。"""

    first_url = _read_first_url(url_file)
    parsed = urlparse(first_url)
    host = parsed.netloc or "URL 文件内完整地址"
    path = parsed.path or "URL 文件内图片地址"
    return host, path


def _build_extra_note(summary: dict[str, object], args: argparse.Namespace) -> str:
    """生成写入中文报告的补充统计说明。"""

    if not summary:
        return "未读取到图片压测辅助统计 JSON，请查看 Locust 原生日志。"

    return (
        f"本次目标覆盖 URL 数 {summary.get('total_urls', '-')} 条，"
        f"实际开始请求 {summary.get('started_requests', '-')} 条，"
        f"完成请求 {summary.get('completed_requests', '-')} 条，"
        f"图片响应断言成功 {summary.get('image_success', '-')} 条，"
        f"图片响应断言失败 {summary.get('image_failure', '-')} 条，"
        f"服务端响应头 x-source=javaapi 数量 "
        f"{summary.get('response_x_source_javaapi', '-')}；"
        f"客户端未主动发送 x-source 请求头，URL 上限参数为 {args.url_limit}，"
        f"请求传输模式为 {args.transport}，读取模式为 {args.read_mode}。"
    )


def _run_locust(
    raw_dir: Path,
    summary_file: Path,
    failure_details_file: Path,
    request_log_file: Path,
    download_dir: Path,
    download_log_file: Path,
    args: argparse.Namespace,
) -> int:
    """执行 Locust 图片 URL 压测。"""

    env = os.environ.copy()
    env["LOCUST_IMAGE_URL_FILE"] = args.url_file
    env["LOCUST_IMAGE_URL_LIMIT"] = str(args.url_limit)
    env["LOCUST_IMAGE_SUMMARY_FILE"] = str(summary_file)
    env["LOCUST_IMAGE_FAILURE_DETAILS_FILE"] = str(failure_details_file)
    env["LOCUST_IMAGE_REQUEST_LOG_FILE"] = str(request_log_file)
    env["LOCUST_IMAGE_DOWNLOAD_DIR"] = str(download_dir)
    env["LOCUST_IMAGE_DOWNLOAD_LOG_FILE"] = str(download_log_file)
    env["LOCUST_IMAGE_TRANSPORT"] = args.transport
    env["LOCUST_IMAGE_READ_MODE"] = args.read_mode

    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "locustfiles/locust_image_url_stress.py",
        "--headless",
        "-u",
        str(args.users),
        "-r",
        str(args.spawn_rate),
        "--csv",
        str(raw_dir / "stats"),
        "--html",
        str(raw_dir / "report.html"),
    ]
    if args.max_time:
        command.extend(["-t", args.max_time])

    return subprocess.run(command, check=False, env=env).returncode


def _build_context(summary: dict[str, object], args: argparse.Namespace) -> ReportContext:
    """构造图片 URL 压测中文报告上下文。"""

    host, path = _derive_url_scope(args.url_file)
    return ReportContext(
        scenario=args.scenario,
        test_type=args.test_type,
        overview="针对图片 URL 批量访问能力进行压力测试，验证图片资源在目标并发下的 HTTP 响应、失败率和读取日志。",
        plan=(
            f"读取 {args.url_file} 前 {args.url_limit} 条 URL，"
            f"以 {args.users} 并发、每秒 {args.spawn_rate} 用户爬坡执行；"
            "每条 URL 只访问一次，全部覆盖后自动停止。"
        ),
        environment=args.environment,
        executor=args.executor,
        users=str(args.users),
        spawn_rate=f"{args.spawn_rate} user/s",
        run_time="覆盖完成自动停止",
        host=host,
        data_file=args.url_file,
        api_name="图片 URL 加载",
        method="GET",
        path=path,
        success_criteria=(
            "HTTP 200、Content-Type 为 image/*、Content-Length 合法且响应体非空；"
            "full 读取模式下还要求实际读取字节数等于 Content-Length。"
        ),
        extra_note=_build_extra_note(summary, args),
    )


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(description="Run image URL stress test and build CN report.")
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--spawn-rate", type=float, default=5)
    parser.add_argument("--url-limit", type=int, default=2000)
    parser.add_argument("--url-file", default="data/examples/url_sample.txt")
    parser.add_argument("--scenario", default="image_url")
    parser.add_argument("--test-type", default="stress")
    parser.add_argument("--environment", default="test")
    parser.add_argument("--executor", default="QA")
    parser.add_argument("--max-time", default="")
    parser.add_argument("--transport", choices=["urllib", "requests"], default="requests")
    parser.add_argument("--read-mode", choices=["first_chunk", "full", "head"], default="first_chunk")
    parser.add_argument("--baseline-stats-csv", default=None, type=Path)
    parser.add_argument("--no-llm-analysis", action="store_true")
    args = parser.parse_args()

    ts = _timestamp()
    raw_dir = ensure_unique_path(Path("reports") / "raw" / args.scenario / ts)
    raw_dir.mkdir(parents=True, exist_ok=True)
    summary_file = raw_dir / "image_url_summary.json"
    failure_details_file = raw_dir / "failure_details.jsonl"
    download_dir = raw_dir / "downloaded_images"
    download_log_file = raw_dir / "download_results.jsonl"
    request_log_file = ensure_unique_path(Path("logs") / f"image_url_requests_{ts}.jsonl")
    request_log_file.parent.mkdir(parents=True, exist_ok=True)

    locust_return_code = _run_locust(
        raw_dir,
        summary_file,
        failure_details_file,
        request_log_file,
        download_dir,
        download_log_file,
        args,
    )

    stats_csv = raw_dir / "stats_stats.csv"
    if not stats_csv.exists():
        raise RuntimeError(
            f"Locust 执行失败且未生成 stats CSV，退出码={locust_return_code}，"
            f"请查看原始产物目录: {raw_dir}"
        )

    summary = _read_summary(summary_file)
    html_report = build_report(
        _build_context(summary, args),
        stats_csv,
        current_timestamp=ts,
        failure_details_file=failure_details_file,
        history_file=raw_dir / "stats_stats_history.csv",
        image_summary_file=summary_file,
        request_log_file=request_log_file,
        baseline_stats_csv=args.baseline_stats_csv,
        enable_llm_analysis=not args.no_llm_analysis,
    )

    if locust_return_code != 0:
        print(
            f"Locust 退出码为 {locust_return_code}，通常表示本次压测存在失败请求；"
            "已继续生成中文报告，请以报告中的失败率和失败明细判断结果。"
        )
    print(f"Locust 原始产物目录: {raw_dir}")
    print(f"图片辅助统计 JSON: {summary_file}")
    print(f"失败明细 JSONL: {failure_details_file}")
    print(f"图片下载目录: {download_dir}")
    print(f"图片下载结果 JSONL: {download_log_file}")
    print(f"全量请求日志 JSONL: {request_log_file}")
    print(f"中文 HTML 报告: {html_report}")


if __name__ == "__main__":
    main()
