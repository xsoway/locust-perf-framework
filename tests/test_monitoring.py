#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""监控数据解析与报告渲染测试（自包含，不触网）。"""

from __future__ import annotations

from pathlib import Path

from tools.monitoring_parser import (
    MonitoringSeries,
    SlowQuery,
    align_timeline,
    parse_prometheus_csv,
    parse_slow_query_jsonl,
)
from tools.report_builder import _monitoring_section


def test_parse_prometheus_csv_reads_metrics(tmp_path: Path) -> None:
    """能从 Prometheus 导出 CSV 解析 CPU/内存/吞吐样本。"""

    csv_file = tmp_path / "metrics.csv"
    csv_file.write_text(
        "timestamp,cpu,memory,rps\n"
        "1720000000,10,40,100\n"
        "1720000060,55,70,300\n",
        encoding="utf-8",
    )

    samples = parse_prometheus_csv(csv_file)

    assert len(samples) == 2
    assert samples[0].cpu == 10.0
    assert samples[1].memory == 70.0
    assert samples[1].rps == 300.0


def test_parse_prometheus_csv_sort_by_time(tmp_path: Path) -> None:
    """返回的样本应按时间戳升序。"""

    csv_file = tmp_path / "unsorted.csv"
    csv_file.write_text(
        "time,cpu\n1720000120,50\n1720000000,10\n1720000060,20\n",
        encoding="utf-8",
    )

    samples = parse_prometheus_csv(csv_file)

    timestamps = [s.timestamp for s in samples]
    assert timestamps == sorted(timestamps)


def test_parse_slow_query_jsonl(tmp_path: Path) -> None:
    """能解析慢查询 JSONL 并支持 ISO 时间戳。"""

    jsonl = tmp_path / "slow.jsonl"
    jsonl.write_text(
        '{"timestamp": "2026-06-24T17:00:00", "duration_ms": 1200, "query": "SELECT * FROM orders"}\n'
        '{"timestamp": "2026-06-24T17:00:05", "duration_ms": 500, "query": "SELECT id FROM users"}\n',
        encoding="utf-8",
    )

    queries = parse_slow_query_jsonl(jsonl)

    assert len(queries) == 2
    assert queries[0].duration_ms == 1200.0
    assert queries[0].timestamp < queries[1].timestamp  # 按时间升序


def test_align_timeline_builds_series(tmp_path: Path) -> None:
    """资源样本与慢查询能合成一个 MonitoringSeries。"""

    metrics = tmp_path / "m.csv"
    metrics.write_text("timestamp,cpu,rps\n1720000000,10,100\n1720000060,60,310\n", encoding="utf-8")
    slow = tmp_path / "s.jsonl"
    slow.write_text('{"timestamp": 1720000030, "duration_ms": 900, "query": "SELECT * FROM t"}\n', encoding="utf-8")

    series = align_timeline(parse_prometheus_csv(metrics), slow)

    assert series.timestamps == [1720000000, 1720000060]
    assert series.cpu == [10.0, 60.0]
    assert len(series.slow_queries) == 1


def test_align_timeline_empty_series_is_empty() -> None:
    """无任何数据时 is_empty 应为 True。"""

    series = align_timeline([])

    assert series.is_empty is True


def test_monitoring_section_renders_empty_string_without_data() -> None:
    """没有监控数据时 _monitoring_section 应返回空串。"""

    assert _monitoring_section(MonitoringSeries()) == ""


def test_monitoring_section_renders_svg_and_slow_table() -> None:
    """有监控数据时渲染 SVG 与慢查询表。"""

    series = MonitoringSeries(
        timestamps=[1720000000.0, 1720000060.0],
        cpu=[10.0, 60.0],
        rps=[100.0, 310.0],
        slow_queries=[
            SlowQuery(timestamp=1720000030.0, duration_ms=900.0, query="SELECT * FROM t"),
        ],
    )

    html = _monitoring_section(series)

    assert "<svg" in html
    assert "慢查询" in html
    assert "SELECT" in html
    assert "section" in html