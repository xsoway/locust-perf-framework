#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多源监控数据解析与时间戳对齐工具。

作用：把 Locust 压力结果之外的"第二层证据"（服务器 CPU / 内存 / 数据库慢查询）
解析成统一时间轴，供报告做"RT 与资源关联"交叉分析。

支持两类输入：
- Prometheus 导出 CSV（`timestamp_seconds, metric, value, ...`）
- 慢查询 JSONL（每行一条 `{"timestamp": "...", "duration_ms": 123, "query": "..."}`）

所有解析函数为纯函数、不触网，便于测试与复用。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class MetricSample:
    """一条时间序列资源指标样本。"""

    timestamp: float  # 秒级 epoch（对齐用）
    cpu: float | None = None  # 0-100
    memory: float | None = None  # 0-100
    disk_io: float | None = None  # MB/s
    rps: float | None = None  # 被测吞吐，可选


@dataclass(frozen=True)
class SlowQuery:
    """一条慢查询记录。"""

    timestamp: float  # 秒级 epoch
    duration_ms: float
    query: str


@dataclass
class MonitoringSeries:
    """对齐后的监控时间序列。"""

    timestamps: list[float] = field(default_factory=list)
    cpu: list[float] = field(default_factory=list)
    memory: list[float] = field(default_factory=list)
    rps: list[float] = field(default_factory=list)
    slow_queries: list[SlowQuery] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """是否没有任何可展示的数据点。"""

        return not self.timestamps and not self.slow_queries


def _epoch_seconds(value: float | int | str | datetime) -> float:
    """统一把时间值转成秒级 epoch。"""

    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        # 支持 ISO 8601（含或不含毫秒/时区）与纯秒数字符串
        try:
            return float(value)
        except ValueError:
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(value, fmt).timestamp()
                except ValueError:
                    continue
        raise ValueError(f"无法解析时间戳: {value}")
    return float(value)


def parse_prometheus_csv(path: Path) -> list[MetricSample]:
    """解析 Prometheus 导出的单调时序 CSV。

    兼容常见导出列：`timestamp` / `time` / `t`（时间）、`cpu` / `cpu_usage`、
    `memory` / `mem_usage`、`disk_io`、`rps` / `qps`。无法解析的列忽略。
    """

    samples: list[MetricSample] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return samples
        lower = {name.strip().lower(): name for name in reader.fieldnames}
        for row in reader:
            try:
                ts = _pick(row, lower, "timestamp", "time", "t", "ts")
                if ts is None:
                    continue
                samples.append(
                    MetricSample(
                        timestamp=_epoch_seconds(ts),
                        cpu=_pick_float(row, lower, "cpu", "cpu_usage"),
                        memory=_pick_float(row, lower, "memory", "mem_usage"),
                        disk_io=_pick_float(row, lower, "disk_io", "disk"),
                        rps=_pick_float(row, lower, "rps", "qps", "tps"),
                    )
                )
            except (ValueError, KeyError):
                continue
    samples.sort(key=lambda s: s.timestamp)
    return samples


def _pick(row: dict[str, str], lower: dict[str, str], *names: str) -> str | None:
    """从 CSV 行中按列名（小写）取首个存在的值。"""

    for name in names:
        key = lower.get(name)
        if key and key in row and row[key].strip():
            return row[key].strip()
    return None


def _pick_float(row: dict[str, str], lower: dict[str, str], *names: str) -> float | None:
    """从 CSV 行中按列名取值并转为 float；取不到或解析失败返回 None。"""

    raw = _pick(row, lower, *names)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return None


def parse_slow_query_jsonl(path: Path, truncated_query: int = 200) -> list[SlowQuery]:
    """解析慢查询 JSONL，每行一条记录。

    timestamp 可为 ISO 8601 字符串或秒级 epoch；duration_ms 为慢查询耗时。
    """

    records: list[SlowQuery] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "timestamp" not in obj or "duration_ms" not in obj:
                continue
            try:
                records.append(
                    SlowQuery(
                        timestamp=_epoch_seconds(obj["timestamp"]),
                        duration_ms=float(obj["duration_ms"]),
                        query=str(obj.get("query", ""))[:truncated_query],
                    )
                )
            except (ValueError, TypeError):
                continue
    records.sort(key=lambda q: q.timestamp)
    return records


def align_timeline(
    metric_samples: list[MetricSample],
    slow_query_jsonl: Path | None = None,
    *,
    min_samples: int = 5,
) -> MonitoringSeries:
    """把资源指标与慢查询对齐到统一时间轴。

    以资源样本时间戳为主轴，按时间窗口把慢查询归并到最近的样本点上。
    """

    series = MonitoringSeries()
    for s in metric_samples:
        series.timestamps.append(s.timestamp)
        series.cpu.append(s.cpu if s.cpu is not None else 0.0)
        series.memory.append(s.memory if s.memory is not None else 0.0)
        series.rps.append(s.rps if s.rps is not None else 0.0)

    if slow_query_jsonl is not None and slow_query_jsonl.exists():
        series.slow_queries = parse_slow_query_jsonl(slow_query_jsonl)

    # 归一化空数据（timestamps 为空但只有慢查询时也视为非空）
    if not series.timestamps and series.slow_queries:
        return series

    if series.timestamps and len(series.timestamps) < min_samples:
        # 样本过少时无法画趋势，仍保留，由上层决定是否展示
        return series

    return series