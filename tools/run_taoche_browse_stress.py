#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time     : 2026/07/22 15:30
# @Filename : run_taoche_browse_stress.py
# @Author   : Alan_Hsu
"""二手车浏览场景压力测试与中文报告一体化执行入口。

用法（desktop 站，默认）：
    uv run python -m tools.run_taoche_browse_stress \\
        --users 50 --spawn-rate 5 --run-time 5m

    mobile 站模式：
    uv run python -m tools.run_taoche_browse_stress \\
        --site mobile --users 50 --spawn-rate 5 --run-time 5m

    跳过 LLM 分析：
    uv run python -m tools.run_taoche_browse_stress \\
        --users 50 --spawn-rate 5 --run-time 5m --no-llm-analysis

    指定数据文件（覆盖 --site 的默认文件）：
    uv run python -m tools.run_taoche_browse_stress \\
        --data-file data/examples/taoche_browse_urls.csv
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.naming import ensure_unique_path
from tools.report_builder import ReportContext, build_report

# ---------- 站点模式配置 ----------

# 每个站点对应的默认 host 与数据文件（脱敏示例，真实站点的 host/URL 用
# --host / --data-file 覆盖，或放到 data/ 外的本地文件，避免写入仓库）
_SITE_CONFIG: dict[str, dict[str, str]] = {
    "desktop": {
        "host": "https://example.com",
        "data_file": "data/examples/taoche_browse_urls.csv",
    },
    "mobile": {
        "host": "https://example.com",
        "data_file": "data/examples/taoche_browse_urls.csv",
    },
}

DEFAULT_HOST = _SITE_CONFIG["desktop"]["host"]
DEFAULT_DATA_FILE = _SITE_CONFIG["desktop"]["data_file"]


def _timestamp() -> str:
    """返回本次执行批次时间戳。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_summary(summary_file: Path) -> dict[str, object]:
    """读取压测辅助统计 JSON。"""
    if not summary_file.exists():
        return {}
    return json.loads(summary_file.read_text(encoding="utf-8"))


def _build_extra_note(summary: dict[str, object], site: str) -> str:
    """生成写入中文报告的补充统计说明。"""
    if not summary:
        return "未读取到压测辅助统计 JSON。"
    request_counts = summary.get("request_counts", {})
    total = summary.get("request_total", 0)
    detail = ", ".join(
        f"{k}={v}" for k, v in sorted(request_counts.items())
    )

    # 根据站点模式输出对应的流量模型描述
    if site == "desktop":
        traffic_model = (
            "流量模型为加权随机：首页 10%、全部列表 20%、品牌筛选 10%、"
            "多条件筛选 20%、详情页 20%、参数配置 10%、城市切换 10%。"
        )
    else:
        traffic_model = (
            "流量模型为加权随机：首页 8%、全部列表 16%、品牌筛选 8%、"
            "多条件筛选 16%、方案页 8%、大图页 8%、个人中心 8%、"
            "详情页 21%、参数配置 8%。"
        )

    return (
        f"URL 类型数 {summary.get('total_url_types', '-')}，"
        f"总请求数 {total}，各类型请求分布：{detail}。"
        f"{traffic_model}"
    )


def _run_locust(
    raw_dir: Path,
    summary_file: Path,
    failure_details_file: Path,
    args: argparse.Namespace,
) -> int:
    """执行 Locust 压力测试。"""
    env = os.environ.copy()
    env["LOCUST_TAOCHE_FAILURE_DETAILS_FILE"] = str(failure_details_file)
    env["LOCUST_TAOCHE_SUMMARY_FILE"] = str(summary_file)
    env["LOCUST_TAOCHE_URL_FILE"] = args.data_file

    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "locustfiles/locust_taoche_browse_stress.py",
        "--headless",
        "--host",
        args.host,
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


def _build_context(
    args: argparse.Namespace,
    summary: dict[str, object] | None = None,
) -> ReportContext:
    """构造浏览场景中文报告上下文。"""
    site = getattr(args, "site", "desktop")

    # 构造站点相关的描述文本
    if site == "desktop":
        overview = (
            "针对桌面站二手车浏览核心链路（首页、列表、详情、参数配置、城市切换）"
            "进行压力测试，验证各页面在目标并发下的响应时间、失败率和稳定性。"
        )
        pages_description = "9 个典型页面"
        path_description = "多页面路径"
        api_name = "桌面站浏览（首页/列表/详情/参数配置/城市切换）"
    else:
        overview = (
            "针对移动站浏览核心链路（首页、列表、详情、参数配置、"
            "方案页、大图页、个人中心）进行压力测试，"
            "验证各页面在目标并发下的响应时间、失败率和稳定性。"
        )
        pages_description = "12 个典型页面"
        path_description = "多页面路径"
        api_name = "移动站浏览（首页/列表/详情/参数配置/方案页/大图页/个人中心）"

    # 从 summary 中构建补充统计说明
    if summary:
        extra_note = _build_extra_note(summary, site)
    else:
        extra_note = "本脚本只记录 HTTP 状态码级别的失败，不校验响应体内容。"

    return ReportContext(
        scenario=args.scenario,
        test_type=args.test_type,
        overview=overview,
        plan=(
            f"以 {args.users} 并发、每秒 {args.spawn_rate} 用户爬坡执行，"
            f"持续 {args.run_time}；用户按加权随机模式访问 {pages_description}，"
            f"URL 配置从 {args.data_file} 读取，目标 host 为 {args.host}。"
        ),
        environment=args.environment,
        executor=args.executor,
        users=str(args.users),
        spawn_rate=f"{args.spawn_rate} user/s",
        run_time=args.run_time,
        host=args.host,
        data_file=args.data_file,
        api_name=api_name,
        method="GET",
        path=path_description,
        success_criteria="HTTP 200，各页面响应正常无 5xx 错误。",
        extra_note=extra_note,
    )


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="Run taoche browse stress test and build CN report.",
    )
    parser.add_argument(
        "--site",
        default="desktop",
        choices=["desktop", "mobile"],
        help="站点模式：desktop（桌面站，默认）或 mobile（移动站）",
    )
    parser.add_argument("--users", type=int, default=50, help="并发用户数")
    parser.add_argument("--spawn-rate", type=float, default=5, help="每秒启动用户数")
    parser.add_argument("--run-time", default="5m", help="压测持续时间，例如 5m、10m")
    parser.add_argument("--host", default=None, help="目标 host（默认根据 --site 自动选择）")
    parser.add_argument("--data-file", default=None, help="URL 数据文件（默认根据 --site 自动选择）")
    parser.add_argument("--scenario", default="taoche_browse", help="场景名")
    parser.add_argument("--test-type", default="stress", help="测试类型")
    parser.add_argument("--environment", default="test", help="测试环境")
    parser.add_argument("--executor", default="QA", help="执行人")
    parser.add_argument(
        "--baseline-stats-csv",
        default=None,
        type=Path,
        help="历史基线 stats CSV",
    )
    parser.add_argument(
        "--no-llm-analysis",
        action="store_true",
        help="跳过 LLM 辅助分析",
    )
    args = parser.parse_args()

    # 根据 --site 自动填充 host 和 data-file 的默认值
    site_config = _SITE_CONFIG[args.site]
    if args.host is None:
        args.host = site_config["host"]
    if args.data_file is None:
        args.data_file = site_config["data_file"]

    # 创建时间戳目录
    ts = _timestamp()
    raw_dir = ensure_unique_path(Path("reports") / "raw" / args.scenario / ts)
    raw_dir.mkdir(parents=True, exist_ok=True)
    summary_file = raw_dir / "taoche_browse_summary.json"
    failure_details_file = raw_dir / "failure_details.jsonl"

    # 执行 Locust 压测
    locust_return_code = _run_locust(
        raw_dir,
        summary_file,
        failure_details_file,
        args,
    )

    # 生成中文报告
    stats_csv = raw_dir / "stats_stats.csv"
    if not stats_csv.exists():
        raise RuntimeError(
            f"Locust 执行失败且未生成 stats CSV，退出码={locust_return_code}，"
            f"请查看原始产物目录: {raw_dir}"
        )

    summary = _read_summary(summary_file)
    html_report = build_report(
        _build_context(args, summary),
        stats_csv,
        current_timestamp=ts,
        failure_details_file=failure_details_file,
        history_file=raw_dir / "stats_stats_history.csv",
        image_summary_file=summary_file,
        baseline_stats_csv=args.baseline_stats_csv,
        enable_llm_analysis=not args.no_llm_analysis,
    )

    # 输出结果
    if locust_return_code != 0:
        print(
            f"Locust 退出码为 {locust_return_code}，通常表示本次压测存在失败请求；"
            "已继续生成中文报告，请以报告中的失败率和失败明细判断结果。"
        )
    print(f"目标 Host: {args.host}")
    print(f"Locust 原始产物目录: {raw_dir}")
    print(f"失败明细 JSONL: {failure_details_file}")
    print(f"中文 HTML 报告: {html_report}")

    # 打印简单执行命令回顾
    print()
    print("快速复现命令：")
    print(
        f"  uv run python -m tools.run_taoche_browse_stress "
        f"--users {args.users} --spawn-rate {args.spawn_rate} "
        f"--run-time {args.run_time}"
    )


if __name__ == "__main__":
    main()
