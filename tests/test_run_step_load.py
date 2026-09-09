#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶梯加压执行封装与拐点判定测试。"""

from __future__ import annotations

from pathlib import Path

from tools.run_step_load import (
    StepResult,
    detect_breakdown_point,
    detect_breakout_point,
    parse_locust_stats_csv,
)


def test_parse_locust_stats_csv_reads_core_metrics(tmp_path: Path) -> None:
    """能从 Locust stats CSV 解析总览行核心指标。"""

    stats = tmp_path / "stats_stats.csv"
    stats.write_text(
        "Type,Name,# requests,# fails,Median,90%ile,95%ile,99%ile,Average,Min,Max,"
        "Average size,Current RPS,Current failures/s\n"
        "GET,/health,1000,20,12,23,30,80,15,1,120,0,500,3\n",
        encoding="utf-8",
    )

    result = parse_locust_stats_csv(stats)

    assert result.total_requests == 1000
    assert result.total_failures == 20
    assert result.p95_ms == 30
    assert result.p99_ms == 80
    assert result.avg_ms == 15
    assert result.rps == 500
    assert result.error_rate == 0.02


def test_parse_locust_stats_csv_zero_requests_no_division_error(tmp_path: Path) -> None:
    """无请求时错误率应为 0，不应抛除零异常。"""

    stats = tmp_path / "empty_stats.csv"
    stats.write_text("Type,Name,# requests,# fails\nGET,/health,0,0\n", encoding="utf-8")

    result = parse_locust_stats_csv(stats)

    assert result.total_requests == 0
    assert result.error_rate == 0.0


def test_detect_breakdown_point_returns_flat_rps_level() -> None:
    """TPS 不再随并发增长时，返回该拐点并发数。"""

    results = [
        StepResult(users=10, total_requests=1, total_failures=0, error_rate=0.0, rps=100.0, p95_ms=10, p99_ms=20, avg_ms=8),
        StepResult(users=20, total_requests=1, total_failures=0, error_rate=0.0, rps=105.0, p95_ms=20, p99_ms=40, avg_ms=12),
        StepResult(users=30, total_requests=1, total_failures=0, error_rate=0.0, rps=106.0, p95_ms=60, p99_ms=120, avg_ms=25),
    ]

    # 20->30 RPS 增长 (106-105)/105≈1% ≤5%，拐点在 20
    assert detect_breakdown_point(results) == 20


def test_detect_breakdown_point_none_when_still_growing() -> None:
    """TPS 持续增长时不应误报拐点。"""

    results = [
        StepResult(users=10, total_requests=1, total_failures=0, error_rate=0.0, rps=100.0, p95_ms=10, p99_ms=20, avg_ms=8),
        StepResult(users=20, total_requests=1, total_failures=0, error_rate=0.0, rps=160.0, p95_ms=20, p99_ms=40, avg_ms=12),
        StepResult(users=30, total_requests=1, total_failures=0, error_rate=0.0, rps=200.0, p95_ms=30, p99_ms=60, avg_ms=15),
    ]

    assert detect_breakdown_point(results) is None


def test_detect_breakout_point_flags_high_error_level() -> None:
    """错误率超过阈值时返回击穿点并发数。"""

    results = [
        StepResult(users=10, total_requests=1, total_failures=0, error_rate=0.01, rps=100.0, p95_ms=10, p99_ms=20, avg_ms=8),
        StepResult(users=50, total_requests=1, total_failures=1, error_rate=0.08, rps=120.0, p95_ms=30, p99_ms=60, avg_ms=15),
    ]

    assert detect_breakout_point(results, error_threshold=0.05) == 50