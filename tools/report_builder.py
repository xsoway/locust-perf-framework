#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Locust CSV 二次分析报告生成器。"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.metadata
import json
import platform
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from tools.logging_setup import get_logger
from tools.naming import build_report_paths

logger = get_logger(__name__)

BASELINE_TYPES = {"baseline", "基准测试"}
LOAD_STRESS_TYPES = {"load", "stress", "负载测试", "压力测试"}
PLACEHOLDER_HOSTS = {"", "-", "URL 文件内完整地址"}
PLACEHOLDER_PATHS = {"", "-", "URL 文件内图片地址"}


@dataclass(frozen=True)
class LocustStatsRow:
    """Locust stats CSV 中的一行接口指标。"""

    method: str
    name: str
    request_count: int
    failure_count: int
    avg_ms: float
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    rps: float

    @property
    def error_rate(self) -> float:
        """返回错误率。"""

        if self.request_count <= 0:
            return 0.0
        return self.failure_count / self.request_count


@dataclass(frozen=True)
class ReportContext:
    """报告中的业务与执行上下文。"""

    scenario: str
    test_type: str
    overview: str
    plan: str
    environment: str
    executor: str
    users: str
    spawn_rate: str
    run_time: str
    host: str
    data_file: str
    api_name: str
    method: str
    path: str
    success_criteria: str
    extra_note: str = ""

    @property
    def test_type_label(self) -> str:
        """返回中文测试类型。"""

        mapping = {
            "baseline": "基准测试",
            "load": "负载测试",
            "stress": "压力测试",
            "stability": "稳定性测试",
            "peak": "峰值测试",
        }
        return mapping.get(self.test_type, self.test_type)


@dataclass(frozen=True)
class FailureDetail:
    """失败请求明细。"""

    method: str
    interface: str
    url: str
    query: dict[str, str]
    failure_reason: str
    read_mode: str
    client_rt_ms: float
    server_rt_ms: float | None
    body_read_ms: float
    status_code: int | str
    content_type: str
    response_x_source: str
    content_length: int
    body_snippet: str


@dataclass(frozen=True)
class LlmAnalysis:
    """LLM 辅助分析结果。"""

    text: str
    enabled: bool = False
    error: str = ""


@dataclass(frozen=True)
class TrendPoint:
    """Locust history CSV 中的趋势点。"""

    timestamp: str
    user_count: int
    rps: float
    failures_per_second: float
    p95_ms: float
    avg_ms: float
    total_requests: int
    total_failures: int


@dataclass(frozen=True)
class RequestLogStats:
    """全量请求日志聚合统计。"""

    record_count: int = 0
    response_x_source_javaapi: int = 0
    avg_client_rt_ms: float = 0.0
    avg_request_send_ms: float = 0.0
    avg_server_rt_ms: float = 0.0
    server_rt_count: int = 0
    avg_body_read_ms: float = 0.0
    avg_post_process_ms: float = 0.0
    avg_content_length: float = 0.0
    read_mode_counts: dict[str, int] | None = None


def _to_int(value: str | None) -> int:
    if value in {None, "", "N/A"}:
        return 0
    return int(float(value or 0))


def _to_float(value: str | None) -> float:
    if value in {None, "", "N/A"}:
        return 0.0
    return float(value or 0)


def _to_optional_float(value: object) -> float | None:
    """把 JSON 值转换为可缺省浮点数。"""

    if value in {None, "", "N/A"}:
        return None
    return float(value or 0)


def _escape(value: str | int | float) -> str:
    return html.escape(str(value))


def _fmt_number(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _fmt_percent(value: float) -> str:
    return f"{value:.2%}"


def parse_locust_stats(stats_csv: Path) -> list[LocustStatsRow]:
    """解析 Locust `*_stats.csv`。"""

    rows: list[LocustStatsRow] = []
    with stats_csv.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for item in reader:
            name = item.get("Name", "")
            if name == "Aggregated":
                continue
            rows.append(
                LocustStatsRow(
                    method=item.get("Type", ""),
                    name=name,
                    request_count=_to_int(item.get("Request Count")),
                    failure_count=_to_int(item.get("Failure Count")),
                    avg_ms=_to_float(item.get("Average Response Time")),
                    min_ms=_to_float(item.get("Min Response Time")),
                    max_ms=_to_float(item.get("Max Response Time")),
                    p50_ms=_to_float(item.get("50%")),
                    p95_ms=_to_float(item.get("95%")),
                    p99_ms=_to_float(item.get("99%")),
                    rps=_to_float(item.get("Requests/s")),
                )
            )
    return rows


def parse_failure_details(details_file: Path | None) -> list[FailureDetail]:
    """解析失败请求明细 JSONL。"""

    if not details_file or not details_file.exists():
        return []

    details: list[FailureDetail] = []
    with details_file.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            item = json.loads(line)
            response = item.get("response", {})
            error = item.get("error", {})
            failure_reason = _enhanced_failure_reason(
                str(item.get("failure_reason", "")),
                response,
                error,
            )
            details.append(
                FailureDetail(
                    method=str(item.get("method", "")),
                    interface=str(item.get("interface", "")),
                    url=str(item.get("url", "")),
                    query=dict(item.get("query", {})),
                    failure_reason=failure_reason,
                    read_mode=str(item.get("read_mode", "")),
                    client_rt_ms=_to_float(str(item.get("client_rt_ms", 0))),
                    server_rt_ms=_to_optional_float(item.get("server_rt_ms")),
                    body_read_ms=_to_float(str(item.get("body_read_ms", 0))),
                    status_code=response.get("status_code", ""),
                    content_type=str(response.get("content_type", "")),
                    response_x_source=str(response.get("x_source", "")),
                    content_length=_to_int(
                        str(item.get("content_length", response.get("content_length", 0)))
                    ),
                    body_snippet=str(response.get("body_snippet", "")),
                )
            )
    return details


def _enhanced_failure_reason(
    failure_reason: str,
    response: dict[str, object],
    error: dict[str, object],
) -> str:
    """旧明细里 status=0 时优先展示真实请求异常。"""

    if response.get("status_code") != 0:
        return failure_reason
    error_type = str(error.get("type", ""))
    if not error_type:
        return failure_reason
    category = str(error.get("category", "request_exception")) or "request_exception"
    message = str(error.get("message", "")) or "-"
    return f"请求异常 category={category} type={error_type} message={message}"


def parse_history(history_file: Path | None) -> list[TrendPoint]:
    """解析 Locust `*_stats_history.csv` 趋势数据。"""

    if not history_file or not history_file.exists():
        return []

    points: list[TrendPoint] = []
    with history_file.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for item in reader:
            if item.get("Name") != "Aggregated":
                continue
            total_requests = _to_int(item.get("Total Request Count"))
            total_failures = _to_int(item.get("Total Failure Count"))
            points.append(
                TrendPoint(
                    timestamp=str(item.get("Timestamp", "")),
                    user_count=_to_int(item.get("User Count")),
                    rps=_to_float(item.get("Requests/s")),
                    failures_per_second=_to_float(item.get("Failures/s")),
                    p95_ms=_to_float(item.get("95%")),
                    avg_ms=_to_float(item.get("Total Average Response Time")),
                    total_requests=total_requests,
                    total_failures=total_failures,
                )
            )
    return points


def parse_image_summary(summary_file: Path | None) -> dict[str, object]:
    """解析图片压测辅助统计 JSON。"""

    if not summary_file or not summary_file.exists():
        return {}
    return json.loads(summary_file.read_text(encoding="utf-8"))


def parse_request_log_stats(request_log_file: Path | None) -> RequestLogStats:
    """解析全量请求日志，复算响应头统计。"""

    if not request_log_file or not request_log_file.exists():
        return RequestLogStats()

    record_count = 0
    javaapi_count = 0
    client_rt_total = 0.0
    request_send_total = 0.0
    server_rt_total = 0.0
    server_rt_count = 0
    body_read_total = 0.0
    post_process_total = 0.0
    content_length_total = 0.0
    read_mode_counts: Counter[str] = Counter()
    with request_log_file.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record_count += 1
            item = json.loads(line)
            response = item.get("response", {})
            if str(response.get("x_source", "")).strip().lower() == "javaapi":
                javaapi_count += 1
            client_rt_total += _to_float(str(item.get("client_rt_ms", item.get("elapsed_ms", 0))))
            request_send_total += _to_float(str(item.get("request_send_ms", 0)))
            server_rt = _to_optional_float(item.get("server_rt_ms"))
            if server_rt is not None:
                server_rt_total += server_rt
                server_rt_count += 1
            body_read_total += _to_float(str(item.get("body_read_ms", 0)))
            post_process_total += _to_float(str(item.get("post_process_ms", 0)))
            content_length_total += _to_float(
                str(item.get("content_length", response.get("content_length", 0)))
            )
            read_mode = str(item.get("read_mode", "")).strip() or "-"
            read_mode_counts[read_mode] += 1
    return RequestLogStats(
        record_count=record_count,
        response_x_source_javaapi=javaapi_count,
        avg_client_rt_ms=client_rt_total / record_count if record_count else 0.0,
        avg_request_send_ms=request_send_total / record_count if record_count else 0.0,
        avg_server_rt_ms=server_rt_total / server_rt_count if server_rt_count else 0.0,
        server_rt_count=server_rt_count,
        avg_body_read_ms=body_read_total / record_count if record_count else 0.0,
        avg_post_process_ms=post_process_total / record_count if record_count else 0.0,
        avg_content_length=content_length_total / record_count if record_count else 0.0,
        read_mode_counts=dict(read_mode_counts),
    )


def summarize(rows: list[LocustStatsRow]) -> dict[str, float]:
    """汇总核心指标。"""

    total_requests = sum(row.request_count for row in rows)
    total_failures = sum(row.failure_count for row in rows)
    weighted_avg = (
        sum(row.avg_ms * row.request_count for row in rows) / total_requests
        if total_requests
        else 0.0
    )
    return {
        "interface_count": float(len(rows)),
        "total_requests": float(total_requests),
        "total_failures": float(total_failures),
        "error_rate": (total_failures / total_requests) if total_requests else 0.0,
        "avg_ms": weighted_avg,
        "total_rps": sum(row.rps for row in rows),
        "max_ms": max((row.max_ms for row in rows), default=0.0),
        "max_p95_ms": max((row.p95_ms for row in rows), default=0.0),
        "max_p99_ms": max((row.p99_ms for row in rows), default=0.0),
    }


def _slowest_row(rows: list[LocustStatsRow]) -> LocustStatsRow | None:
    return max(rows, key=lambda row: row.p95_ms, default=None)


def _primary_row(rows: list[LocustStatsRow]) -> LocustStatsRow | None:
    """返回最能代表当前报告的接口行。"""

    return max(rows, key=lambda row: row.request_count, default=None)


def _first_failure_url(failure_details: list[FailureDetail]) -> str:
    """返回失败明细中的第一个 URL。"""

    for detail in failure_details:
        if detail.url:
            return detail.url
        if detail.interface:
            return detail.interface
    return ""


def _display_host(context: ReportContext, failure_details: list[FailureDetail]) -> str:
    """返回报告展示用目标域名。"""

    if context.host not in PLACEHOLDER_HOSTS:
        return context.host
    hosts = []
    for detail in failure_details:
        parsed = urlparse(detail.url or detail.interface)
        if parsed.netloc and parsed.netloc not in hosts:
            hosts.append(parsed.netloc)
    if not hosts:
        return "-"
    if len(hosts) == 1:
        return hosts[0]
    return f"{hosts[0]} 等 {len(hosts)} 个域名"


def _display_path(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail],
) -> str:
    """返回报告展示用请求路径。"""

    if context.path not in PLACEHOLDER_PATHS:
        return context.path
    paths = []
    for detail in failure_details:
        parsed = urlparse(detail.url or detail.interface)
        if parsed.path and parsed.path not in paths:
            paths.append(parsed.path)
    if paths:
        if len(paths) == 1:
            return paths[0]
        return f"{paths[0]} 等 {len(paths)} 个路径"
    row = _primary_row(rows)
    if row and row.name:
        return row.name
    return "-"


def _display_api_name(context: ReportContext, rows: list[LocustStatsRow]) -> str:
    """返回报告展示用接口名称。"""

    if context.api_name and context.api_name != "-":
        return context.api_name
    row = _primary_row(rows)
    return row.name if row else context.scenario


def _display_method(context: ReportContext, rows: list[LocustStatsRow]) -> str:
    """返回报告展示用请求方法。"""

    if context.method and context.method != "-":
        return context.method
    row = _primary_row(rows)
    return row.method if row else "-"


def _status_badge(summary: dict[str, float]) -> tuple[str, str, str]:
    error_rate = summary["error_rate"]
    max_p95_ms = summary["max_p95_ms"]
    if error_rate > 0.01:
        return "需修复", "badge-fail", f"失败率 {_fmt_percent(error_rate)}，优先排查失败请求。"
    if max_p95_ms > 3000:
        return "需关注", "badge-warn", f"P95 最高 {_fmt_number(max_p95_ms)} ms，建议确认慢接口原因。"
    return "通过", "badge-pass", "失败率和 P95 响应时间处于默认观察范围内。"


def _callout_class(badge_class: str) -> str:
    """把 badge 样式映射为 callout 样式。"""

    return {
        "badge-pass": "ok",
        "badge-warn": "warn",
        "badge-fail": "fail",
    }.get(badge_class, "")


def _metric_cards(context: ReportContext, rows: list[LocustStatsRow]) -> str:
    summary = summarize(rows)
    status, badge_class, note = _status_badge(summary)
    slowest = _slowest_row(rows)
    is_baseline = context.test_type in BASELINE_TYPES or context.test_type_label in BASELINE_TYPES

    if is_baseline:
        metrics = [
            ("总请求数", f"{summary['total_requests']:.0f}", "Locust Request Count 汇总"),
            ("失败率", _fmt_percent(summary["error_rate"]), "失败数 / 总请求数"),
            ("平均响应", f"{_fmt_number(summary['avg_ms'])} ms", "加权 Average Response Time"),
            ("P95 响应", f"{_fmt_number(summary['max_p95_ms'])} ms", "建立基线时优先观察"),
            ("P99 响应", f"{_fmt_number(summary['max_p99_ms'])} ms", "极端慢请求参考"),
            ("吞吐量", _fmt_number(summary["total_rps"]), "Requests/s 汇总"),
            ("最大响应", f"{_fmt_number(summary['max_ms'])} ms", "Max Response Time"),
            ("状态章", f'<span class="badge {badge_class}">{status}</span>', note),
        ]
    else:
        slowest_name = f"{slowest.method} {slowest.name}" if slowest else "-"
        metrics = [
            ("总请求数", f"{summary['total_requests']:.0f}", "压测样本量"),
            ("失败率", _fmt_percent(summary["error_rate"]), "稳定性和容量判断核心指标"),
            ("吞吐量", _fmt_number(summary["total_rps"]), "Requests/s 汇总"),
            ("最高 P95", f"{_fmt_number(summary['max_p95_ms'])} ms", "压力下慢接口观察"),
            ("最高 P99", f"{_fmt_number(summary['max_p99_ms'])} ms", "尾延迟风险"),
            ("最大响应", f"{_fmt_number(summary['max_ms'])} ms", "极端响应耗时"),
            ("最慢接口", _escape(slowest_name), "按 P95 排序"),
            ("状态章", f'<span class="badge {badge_class}">{status}</span>', note),
        ]

    return "\n".join(
        f"""
        <div class="ledger-cell">
          <div class="ledger-label">{_escape(label)}</div>
          <div class="ledger-value">{value}</div>
          <div class="ledger-note">{_escape(note_text)}</div>
        </div>
        """
        for label, value, note_text in metrics
    )


def _interface_table(rows: list[LocustStatsRow]) -> str:
    table_rows = "\n".join(
        f"""
        <tr class="{_row_class(row)}">
          <td>{_escape(row.method)}</td>
          <td>{_escape(row.name)}</td>
          <td class="num">{row.request_count}</td>
          <td class="num">{row.failure_count}</td>
          <td class="num">{_fmt_percent(row.error_rate)}</td>
          <td class="num">{_fmt_number(row.avg_ms)}</td>
          <td class="num">{_fmt_number(row.p95_ms)}</td>
          <td class="num">{_fmt_number(row.p99_ms)}</td>
          <td class="num">{_fmt_number(row.rps)}</td>
        </tr>
        """
        for row in sorted(rows, key=lambda item: item.p95_ms, reverse=True)
    )
    return table_rows or '<tr><td colspan="9">未解析到接口统计数据</td></tr>'


def _row_class(row: LocustStatsRow) -> str:
    if row.error_rate > 0.01:
        return "fail-row"
    if row.p95_ms > 3000:
        return "warn-row"
    return "ok-row"


def _metric_explanations(context: ReportContext) -> list[tuple[str, str]]:
    if context.test_type in BASELINE_TYPES or context.test_type_label in BASELINE_TYPES:
        return [
            ("平均响应", "用于建立当前接口的日常性能基线，后续版本回归时可直接对比。"),
            ("P95 响应", "比平均值更能反映多数用户体验，适合做基准阈值。"),
            ("P99 响应", "观察少量尾部慢请求，辅助识别偶发抖动。"),
            ("失败率", "基准测试中原则上应接近 0，任何失败都需要确认是否为脚本、数据或服务问题。"),
        ]
    return [
        ("吞吐量", "负载/压力测试中观察系统在目标并发下的处理能力。"),
        ("失败率", "判断系统是否进入不可接受状态的核心指标。"),
        ("P95/P99", "观察高并发下用户体验是否显著劣化。"),
        ("最大响应", "定位极端慢请求或阻塞点，不能单独作为通过/失败依据。"),
    ]


def _metric_interpretation(rows: list[LocustStatsRow]) -> str:
    """渲染关键性能指标的通俗解读。"""

    summary = summarize(rows)
    primary = _primary_row(rows)
    p50_ms = primary.p50_ms if primary else 0.0
    p95_ms = summary["max_p95_ms"]
    p99_ms = summary["max_p99_ms"]
    rps = summary["total_rps"]
    if p50_ms <= 0 and p95_ms <= 0 and p99_ms <= 0 and rps <= 0:
        return '<div class="callout warn">暂无可解读的响应时间与吞吐量数据。</div>'

    tail_gap = max(p99_ms - p50_ms, 0.0)
    if p50_ms > 0 and p99_ms >= p50_ms * 3:
        conclusion = (
            f"大部分请求在 {_fmt_number(p50_ms)} ms 左右完成，但 P95/P99 明显拉高，"
            "说明存在尾部慢请求或网络、服务端处理波动。"
        )
    elif p95_ms > 3000 or p99_ms > 5000:
        conclusion = (
            f"P95 达到 {_fmt_number(p95_ms)} ms，P99 达到 {_fmt_number(p99_ms)} ms，"
            "用户尾部体验偏慢，建议结合失败日志和服务端监控继续定位。"
        )
    else:
        conclusion = (
            f"P50、P95、P99 梯度相对平稳，当前批次下整体响应时间分布较集中；"
            f"吞吐量约 {_fmt_number(rps)} req/s。"
        )

    cards = [
        (
            "Median(P50 RT)",
            f"{_fmt_number(p50_ms)} ms",
            f"表示 50% 的请求响应时间小于等于 {_fmt_number(p50_ms)} ms；也就是一半用户在这个时间内拿到响应。",
        ),
        (
            "req/s 吞吐量",
            f"{_fmt_number(rps)} req/s",
            f"表示系统平均每秒处理 {_fmt_number(rps)} 个请求；值越高，单位时间处理能力越强。",
        ),
        (
            "P95",
            f"{_fmt_number(p95_ms)} ms",
            f"表示 95% 的请求响应时间小于等于 {_fmt_number(p95_ms)} ms；约 5% 的请求会更慢。",
        ),
        (
            "P99",
            f"{_fmt_number(p99_ms)} ms",
            f"表示 99% 的请求响应时间小于等于 {_fmt_number(p99_ms)} ms；用于观察最慢 1% 请求的尾部延迟。",
        ),
    ]
    card_html = "\n".join(
        f"""
        <div class="interpret-card">
          <div class="interpret-name">{_escape(name)}</div>
          <div class="interpret-value">{_escape(value)}</div>
          <p>{_escape(text)}</p>
        </div>
        """
        for name, value, text in cards
    )
    return f"""
    <div class="interpret-panel">
      <h3>关键指标怎么读</h3>
      <div class="interpret-grid">{card_html}</div>
      <div class="callout warn"><strong>综合解读：</strong>{_escape(conclusion)} 尾部延迟差值约 {_fmt_number(tail_gap)} ms。</div>
    </div>
    """


def _bar_chart(
    title: str,
    items: list[tuple[str, float, str] | tuple[str, float, str, str]],
    width: int = 620,
) -> str:
    """渲染横向条形图。"""

    if not items:
        return '<div class="empty-chart">暂无图表数据</div>'

    max_value = max((float(item[1]) for item in items), default=0) or 1
    rows = []
    for item in items:
        label, value, note = item[:3]
        title_text = item[3] if len(item) > 3 else label
        bar_width = max(2, int((value / max_value) * (width - 220)))
        rows.append(
            f"""
            <div class="chart-row">
              <div class="chart-label" title="{_escape(title_text)}">{_escape(label)}</div>
              <div class="chart-track"><span style="width:{bar_width}px"></span></div>
              <div class="chart-value">{_escape(note)}</div>
            </div>
            """
        )
    return f"""
    <div class="chart-box">
      <h3>{_escape(title)}</h3>
      {''.join(rows)}
    </div>
    """


def _short_text(value: str, max_length: int) -> str:
    """返回适合图表标签展示的短文本。"""

    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}..."


def _response_x_source_count(
    context: ReportContext,
    image_summary: dict[str, object] | None = None,
) -> str:
    """优先从辅助统计 JSON 获取 x-source=javaapi 数量。"""

    image_summary = image_summary or {}
    summary_value = image_summary.get("response_x_source_javaapi")
    if summary_value not in {None, ""}:
        return str(summary_value)

    match = re.search(r"x-source=javaapi\s*数量\s*(\d+)", context.extra_note)
    return match.group(1) if match else "-"


def _response_x_source_check(
    image_summary: dict[str, object],
    request_log_stats: RequestLogStats,
) -> str:
    """渲染响应头统计复核说明。"""

    summary_value = image_summary.get("response_x_source_javaapi")
    if summary_value in {None, ""}:
        return "未读取到 image_url_summary.json，无法校验 summary 统计。"
    if request_log_stats.record_count <= 0:
        return "当前批次未提供全量请求日志，仅展示 image_url_summary.json 中的统计值。"

    summary_count = _to_int(str(summary_value))
    log_count = request_log_stats.response_x_source_javaapi
    if summary_count == log_count:
        return f"已用全量请求日志复算 {request_log_stats.record_count} 条，请求日志与 summary 一致。"
    return (
        f"请求日志复算为 {log_count} 次，summary 为 {summary_count} 次，"
        "两者不一致，请优先检查日志是否来自同一批次。"
    )


def _request_timing_breakdown(request_log_stats: RequestLogStats) -> str:
    """渲染请求级耗时拆分指标。"""

    if request_log_stats.record_count <= 0:
        return (
            '<div class="callout warn"><strong>请求级耗时拆分缺失：</strong>'
            "当前报告未读取到 request_details.jsonl，无法展示 client/server/body 指标。</div>"
        )

    read_mode_counts = request_log_stats.read_mode_counts or {}
    read_mode_text = "".join(
        f'<span class="read-mode-pill"><strong>{_escape(mode)}</strong>{count}</span>'
        for mode, count in sorted(read_mode_counts.items())
    )
    server_rt_text = (
        f"{_fmt_number(request_log_stats.avg_server_rt_ms)} ms"
        if request_log_stats.server_rt_count
        else "响应头未提供"
    )
    server_rt_note = (
        f"已解析 {request_log_stats.server_rt_count} 条"
        if request_log_stats.server_rt_count
        else "未参与均值计算"
    )

    return f"""
    <div class="metric-grid">
      <div class="metric-card">
        <span class="metric-card-label">client_rt_ms 平均</span>
        <strong>{_fmt_number(request_log_stats.avg_client_rt_ms)} ms</strong>
        <small>Locust 当前报告 RT 口径，包含网络、等待响应、读取 body 与客户端处理。</small>
      </div>
      <div class="metric-card">
        <span class="metric-card-label">request_send_ms 平均</span>
        <strong>{_fmt_number(request_log_stats.avg_request_send_ms)} ms</strong>
        <small>从发起请求到拿到响应对象的耗时，近似 DNS/TCP/TLS/TTFB 阶段。</small>
      </div>
      <div class="metric-card">
        <span class="metric-card-label">server_rt_ms 平均</span>
        <strong>{_escape(server_rt_text)}</strong>
        <small>来自响应头或服务端日志字段；{_escape(server_rt_note)}。</small>
      </div>
      <div class="metric-card">
        <span class="metric-card-label">body_read_ms 平均</span>
        <strong>{_fmt_number(request_log_stats.avg_body_read_ms)} ms</strong>
        <small>响应头返回后，客户端读取图片 body 的耗时。</small>
      </div>
      <div class="metric-card">
        <span class="metric-card-label">post_process_ms 平均</span>
        <strong>{_fmt_number(request_log_stats.avg_post_process_ms)} ms</strong>
        <small>断言、统计和日志准备等脚本侧处理耗时。</small>
      </div>
      <div class="metric-card">
        <span class="metric-card-label">content_length 平均</span>
        <strong>{_fmt_number(request_log_stats.avg_content_length)} B</strong>
        <small>当前读取模式下实际读取到的字节数；first_chunk 通常不是整图大小。</small>
      </div>
    </div>
    <div class="read-mode-strip">
      <span>read_mode 分布</span>
      <div>{read_mode_text or '<em>-</em>'}</div>
    </div>
    """


def _failure_reason_label(reason: str) -> str:
    """将较长失败原因压缩成适合图表展示的标签。"""

    if "图片内容校验失败" in reason:
        content_type = "-"
        bytes_text = "-"
        content_type_match = re.search(r"content_type=([^\s]+)", reason)
        bytes_match = re.search(r"bytes=(\d+)", reason)
        if content_type_match:
            content_type = content_type_match.group(1).replace("application/", "")
        if bytes_match:
            bytes_text = f"{bytes_match.group(1)}B"
        return f"内容校验失败 / {content_type} / {bytes_text}"
    if "图片请求失败" in reason:
        status_match = re.search(r"status=([^\s]+)", reason)
        status = status_match.group(1) if status_match else "-"
        return f"请求失败 / status={status}"
    return _short_text(reason, 32)


def _failure_group_key(reason: str) -> str:
    """返回用于聚合统计的失败原因，避免被 URL 打散。"""

    category_match = re.search(r"category=([^\s]+)", reason)
    type_match = re.search(r"type=([^\s]+)", reason)
    status_match = re.search(r"status=([^\s]+)", reason)
    if category_match or type_match:
        category = category_match.group(1) if category_match else "request_exception"
        error_type = type_match.group(1) if type_match else "Unknown"
        extra = ""
        if "NameResolutionError" in reason or "Failed to resolve" in reason:
            extra = " / NameResolutionError"
        elif "IncompleteRead" in reason:
            extra = " / IncompleteRead"
        return f"请求异常 / {category} / {error_type}{extra}"
    if status_match:
        return f"请求失败 / status={status_match.group(1)}"
    return reason


def _charts(rows: list[LocustStatsRow], failure_details: list[FailureDetail]) -> str:
    """渲染报告图表区。"""

    summary = summarize(rows)
    success_count = max(summary["total_requests"] - summary["total_failures"], 0)
    result_chart = _bar_chart(
        "请求结果分布",
        [
            ("请求成功", success_count, f"{success_count:.0f}"),
            ("请求失败", summary["total_failures"], f"{summary['total_failures']:.0f}"),
        ],
    )
    latency_chart = _bar_chart(
        "响应时间分位",
        [
            ("平均响应", summary["avg_ms"], f"{_fmt_number(summary['avg_ms'])} ms"),
            ("最高 P95", summary["max_p95_ms"], f"{_fmt_number(summary['max_p95_ms'])} ms"),
            ("最高 P99", summary["max_p99_ms"], f"{_fmt_number(summary['max_p99_ms'])} ms"),
            ("最大响应", summary["max_ms"], f"{_fmt_number(summary['max_ms'])} ms"),
        ],
    )
    reason_counter = Counter(_failure_group_key(detail.failure_reason) for detail in failure_details)
    failure_chart = _bar_chart(
        "失败类型分布",
        [
            (_failure_reason_label(reason), count, str(count), reason)
            for reason, count in reason_counter.most_common(6)
        ],
    )
    return f'<div class="chart-grid">{result_chart}{latency_chart}{failure_chart}</div>'


def _package_version(package_name: str) -> str:
    """读取工具包版本。"""

    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "-"


def _polyline(points: list[float], width: int, height: int) -> str:
    """生成 SVG polyline 坐标。"""

    if not points:
        return ""
    max_value = max(points) or 1
    if len(points) == 1:
        return f"0,{height - (points[0] / max_value) * height:.1f}"
    step = width / (len(points) - 1)
    return " ".join(
        f"{index * step:.1f},{height - (value / max_value) * height:.1f}"
        for index, value in enumerate(points)
    )


def _trend_point_coordinates(values: list[float], width: int, height: int) -> list[tuple[float, float]]:
    """生成趋势图数据点坐标。"""

    if not values:
        return []
    max_value = max(values) or 1
    if len(values) == 1:
        return [(0.0, height - (values[0] / max_value) * height)]
    step = width / (len(values) - 1)
    return [
        (index * step, height - (value / max_value) * height)
        for index, value in enumerate(values)
    ]


def _trend_points_svg(
    title: str,
    points: list[TrendPoint],
    values: list[float],
    unit: str,
    width: int,
    height: int,
) -> str:
    """渲染带悬浮数据卡的趋势图数据点。"""

    coordinates = _trend_point_coordinates(values, width, height)
    circles = []
    tooltip_width = 148
    tooltip_height = 110
    for point, value, (x, y) in zip(points, values, coordinates, strict=False):
        tooltip = (
            f"{title}\n"
            f"时间: {point.timestamp or '-'}\n"
            f"并发用户: {point.user_count}\n"
            f"当前值: {_fmt_number(value)} {unit}\n"
            f"累计请求: {point.total_requests}\n"
            f"累计失败: {point.total_failures}"
        )
        tooltip_x = min(max(x + 10, 0), max(width - tooltip_width, 0))
        tooltip_y = y - tooltip_height - 8
        if tooltip_y < 0:
            tooltip_y = min(y + 12, max(height - tooltip_height, 0))
        tooltip_lines = [
            f"时间: {point.timestamp or '-'}",
            f"当前值: {_fmt_number(value)} {unit}",
            f"并发用户: {point.user_count}",
            f"RPS: {_fmt_number(point.rps)} req/s",
            f"P95: {_fmt_number(point.p95_ms)} ms",
            f"平均RT: {_fmt_number(point.avg_ms)} ms",
            f"失败/s: {_fmt_number(point.failures_per_second)}",
            f"累计: {point.total_requests} / 失败 {point.total_failures}",
        ]
        tooltip_text = "\n".join(
            f'<tspan x="{tooltip_x + 8:.1f}" dy="{12 if index == 0 else 12}">'
            f"{_escape(line)}</tspan>"
            for index, line in enumerate(tooltip_lines)
        )
        circles.append(
            f"""
            <g class="trend-point">
              <circle class="trend-dot-hit" cx="{x:.1f}" cy="{y:.1f}" r="8">
                <title>{_escape(tooltip)}</title>
              </circle>
              <circle class="trend-dot" cx="{x:.1f}" cy="{y:.1f}" r="3">
                <title>{_escape(tooltip)}</title>
              </circle>
              <g class="trend-tooltip" transform="translate(0 0)">
                <rect x="{tooltip_x:.1f}" y="{tooltip_y:.1f}" width="{tooltip_width}" height="{tooltip_height}" rx="4"></rect>
                <text x="{tooltip_x + 8:.1f}" y="{tooltip_y + 10:.1f}">{tooltip_text}</text>
              </g>
            </g>
            """
        )
    return "".join(circles)


def _trend_chart(title: str, points: list[TrendPoint], attr: str, unit: str) -> str:
    """渲染趋势折线图。"""

    if not points:
        return f"""
        <div class="chart-box">
          <h3>{_escape(title)}</h3>
          <div class="empty-chart">未读取到 Locust history CSV，无法展示趋势。</div>
        </div>
        """
    values = [float(getattr(point, attr)) for point in points]
    latest = values[-1] if values else 0.0
    polyline = _polyline(values, 360, 120)
    point_nodes = _trend_points_svg(title, points, values, unit, 360, 120)
    return f"""
    <div class="chart-box">
      <h3>{_escape(title)}</h3>
      <svg class="trend-svg" viewBox="0 0 400 150" role="img" aria-label="{_escape(title)}">
        <line x1="20" y1="130" x2="380" y2="130"></line>
        <line x1="20" y1="10" x2="20" y2="130"></line>
        <polyline points="{polyline}" transform="translate(20 10)"></polyline>
        <g transform="translate(20 10)">{point_nodes}</g>
      </svg>
      <div class="muted">最新值：{_fmt_number(latest)} {unit}</div>
    </div>
    """


def _trend_section(history_points: list[TrendPoint]) -> str:
    """渲染 TPS、RT、错误率趋势区。"""

    error_rates = []
    for point in history_points:
        error_rates.append(
            (point.total_failures / point.total_requests * 100) if point.total_requests else 0.0
        )
    error_points = [
        TrendPoint(
            timestamp=point.timestamp,
            user_count=point.user_count,
            rps=rate,
            failures_per_second=point.failures_per_second,
            p95_ms=point.p95_ms,
            avg_ms=point.avg_ms,
            total_requests=point.total_requests,
            total_failures=point.total_failures,
        )
        for point, rate in zip(history_points, error_rates, strict=False)
    ]
    return f"""
    <div class="chart-grid">
      {_trend_chart("TPS / RPS 趋势", history_points, "rps", "req/s")}
      {_trend_chart("RT P95 趋势", history_points, "p95_ms", "ms")}
      {_trend_chart("错误率趋势", error_points, "rps", "%")}
    </div>
    <div class="callout warn">
      <strong>CPU 利用率关联：</strong>当前执行产物未包含 CPU/内存/网络资源监控数据，
      暂无法绘制 TPS 与 CPU 利用率关联曲线。建议后续接入 Prometheus、系统监控 CSV 或压测机采样日志。
    </div>
    """


def _bottleneck_analysis(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail],
) -> str:
    """渲染瓶颈分析表。"""

    summary = summarize(rows)
    reason_counter = Counter(_failure_group_key(detail.failure_reason) for detail in failure_details)
    top_reason = reason_counter.most_common(1)[0][0] if reason_counter else "暂无失败明细"
    impact = "高" if summary["error_rate"] > 0.05 or summary["max_p95_ms"] > 10000 else "中"
    api_name = _display_api_name(context, rows)
    host = _display_host(context, failure_details)
    return f"""
    <table>
      <thead><tr><th>问题点</th><th>影响程度</th><th>关联指标</th><th>判断依据</th></tr></thead>
      <tbody>
        <tr class="{ 'fail-row' if impact == '高' else 'warn-row' }">
          <td>{_escape(api_name)} 请求链路</td>
          <td>{impact}</td>
          <td>失败率 {_fmt_percent(summary["error_rate"])}；P95 {_fmt_number(summary["max_p95_ms"])} ms；最大响应 {_fmt_number(summary["max_ms"])} ms</td>
          <td>{_escape(top_reason)}</td>
        </tr>
        <tr class="warn-row">
          <td>服务端与下游依赖</td>
          <td>待确认</td>
          <td>需要服务端日志、资源监控、网络指标、下游依赖耗时；目标域名 {_escape(host)}</td>
          <td>当前报告只有客户端侧 Locust 指标，不能单独确认服务端瓶颈归属，可结合 LLM 分析中的排查方向继续定位。</td>
        </tr>
      </tbody>
    </table>
    """


def _topology_highlight(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail],
) -> str:
    """渲染请求链路瓶颈高亮图。"""

    summary = summarize(rows)
    node_class = "hot" if summary["error_rate"] > 0.01 or summary["max_p95_ms"] > 3000 else "ok"
    api_name = _display_api_name(context, rows)
    method = _display_method(context, rows)
    host = _display_host(context, failure_details)
    path = _display_path(context, rows, failure_details)
    return f"""
    <div class="topology">
      <div class="top-node ok">Locust 压测客户端<br><span>{_escape(context.users)} 并发 / {_escape(context.spawn_rate)} 启动</span></div>
      <div class="top-arrow">→</div>
      <div class="top-node ok">{_escape(api_name)}<br><span>{_escape(method)} {_escape(path)}</span></div>
      <div class="top-arrow">→</div>
      <div class="top-node {node_class}">目标服务<br><span>{_escape(host)} · 失败率 {_fmt_percent(summary["error_rate"])} · P95 {_fmt_number(summary["max_p95_ms"])} ms</span></div>
    </div>
    """


def _call_chain_heatmap(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail],
    image_summary: dict[str, object],
    request_log_stats: RequestLogStats,
) -> str:
    """渲染请求级调用链热图。"""

    summary = summarize(rows)
    reason_count = len(failure_details)
    x_source_count = _response_x_source_count(context, image_summary)
    x_source_check = _response_x_source_check(image_summary, request_log_stats)
    api_name = _display_api_name(context, rows)
    method = _display_method(context, rows)
    header_cell = (
        f'<div class="heat-cell warm">响应头统计<br><span>x-source=javaapi：{_escape(x_source_count)} 次</span></div>'
        if x_source_count != "-"
        else f'<div class="heat-cell warm">失败明细<br><span>{reason_count} 条</span></div>'
    )
    header_note = (
        f'<div class="callout warn"><strong>x-source 统计口径：</strong>{_escape(x_source_check)}</div>'
        if x_source_count != "-"
        else ""
    )
    return f"""
    <div class="heatmap">
      <div class="heat-cell cool">请求分配<br><span>{summary["total_requests"]:.0f} 次</span></div>
      <div class="heat-cell hot">{_escape(method)} 请求<br><span>{_escape(api_name)}</span></div>
      <div class="heat-cell hot">响应耗时<br><span>P95 {_fmt_number(summary["max_p95_ms"])} ms</span></div>
      {header_cell}
    </div>
    {header_note}
    <p class="muted">当前数据粒度为请求级，未接入应用 APM/函数追踪，因此无法标明具体耗时函数；后续可接入 TraceId、APM span 或服务端调用链日志。</p>
    """


def _root_cause_table(failure_details: list[FailureDetail]) -> str:
    """渲染原因定位表。"""

    if not failure_details:
        return '<div class="callout warn">暂无失败明细，无法展开日志级原因定位。</div>'
    reason_counter = Counter(_failure_group_key(detail.failure_reason) for detail in failure_details)
    rows = []
    for reason, count in reason_counter.most_common(6):
        samples = [
            detail for detail in failure_details if _failure_group_key(detail.failure_reason) == reason
        ][:2]
        sample_text = "；".join(
            f"{detail.interface} 入参={_query_text(detail.query)} 返回={detail.body_snippet[:80] or '-'}"
            for detail in samples
        )
        rows.append(
            f"""
            <tr class="fail-row">
              <td class="reason-cell">{_escape(reason)}</td>
              <td class="num">{count}</td>
              <td class="break-cell">{_escape(sample_text)}</td>
            </tr>
            """
        )
    return f"""
    <table>
      <thead><tr><th>异常类型</th><th class="num">次数</th><th>日志/返回样例</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _optimization_table(rows: list[LocustStatsRow], failure_details: list[FailureDetail]) -> str:
    """渲染优化建议与优先级。"""

    summary = summarize(rows)
    high_priority = (
        "优先核查失败请求的接口、入参与返回内容，确认是否为鉴权、数据、路由、服务异常或下游依赖问题。"
        if failure_details
        else "优先补充失败明细采集，再进行问题归因。"
    )
    return f"""
    <table>
      <thead><tr><th>优先级</th><th>建议</th><th>触发依据</th></tr></thead>
      <tbody>
        <tr class="fail-row"><td>P0</td><td>{_escape(high_priority)}</td><td>失败率 {_fmt_percent(summary["error_rate"])}；失败明细 {len(failure_details)} 条。</td></tr>
        <tr class="warn-row"><td>P1</td><td>接入服务端日志、网关日志、下游依赖耗时、CPU/内存/网络指标，用于区分客户端压力、服务端处理和依赖瓶颈。</td><td>当前报告缺少资源监控与服务端链路数据。</td></tr>
        <tr class="ok-row"><td>P2</td><td>按 50/100/200 并发阶梯重跑，形成容量曲线和历史对比基线。</td><td>当前单批次不足以判断容量拐点。</td></tr>
      </tbody>
    </table>
    """


def _delta_text(current: float, baseline: float, unit: str = "") -> str:
    """格式化当前值相对基线的差异。"""

    delta = current - baseline
    sign = "+" if delta >= 0 else ""
    return f"{sign}{_fmt_number(delta)}{unit}"


def _history_comparison_section(
    current_rows: list[LocustStatsRow],
    baseline_rows: list[LocustStatsRow] | None = None,
) -> str:
    """渲染历史对比说明。"""

    if not baseline_rows:
        return (
            '<div class="callout warn"><strong>历史对比未启用：</strong>'
            "当前报告未传入历史基线批次。使用方式：执行报告生成命令时增加 "
            '<code>--baseline-stats-csv reports/raw/image_url/&lt;历史批次&gt;/stats_stats.csv</code>，'
            "即可对比总请求数、失败率、平均响应、P95/P99、RPS 和最大响应。</div>"
        )

    current = summarize(current_rows)
    baseline = summarize(baseline_rows)
    error_delta = current["error_rate"] - baseline["error_rate"]
    rows = [
        ("总请求数", f"{current['total_requests']:.0f}", f"{baseline['total_requests']:.0f}", _delta_text(current["total_requests"], baseline["total_requests"])),
        ("失败率", _fmt_percent(current["error_rate"]), _fmt_percent(baseline["error_rate"]), f"{error_delta:+.2%}"),
        ("平均响应", f"{_fmt_number(current['avg_ms'])} ms", f"{_fmt_number(baseline['avg_ms'])} ms", _delta_text(current["avg_ms"], baseline["avg_ms"], " ms")),
        ("最高 P95", f"{_fmt_number(current['max_p95_ms'])} ms", f"{_fmt_number(baseline['max_p95_ms'])} ms", _delta_text(current["max_p95_ms"], baseline["max_p95_ms"], " ms")),
        ("最高 P99", f"{_fmt_number(current['max_p99_ms'])} ms", f"{_fmt_number(baseline['max_p99_ms'])} ms", _delta_text(current["max_p99_ms"], baseline["max_p99_ms"], " ms")),
        ("吞吐量", _fmt_number(current["total_rps"]), _fmt_number(baseline["total_rps"]), _delta_text(current["total_rps"], baseline["total_rps"])),
        ("最大响应", f"{_fmt_number(current['max_ms'])} ms", f"{_fmt_number(baseline['max_ms'])} ms", _delta_text(current["max_ms"], baseline["max_ms"], " ms")),
    ]
    table_rows = "\n".join(
        f"<tr><td>{_escape(name)}</td><td class=\"num\">{_escape(current_value)}</td>"
        f"<td class=\"num\">{_escape(baseline_value)}</td><td class=\"num\">{_escape(delta)}</td></tr>"
        for name, current_value, baseline_value, delta in rows
    )
    return f"""
    <table>
      <thead><tr><th>指标</th><th class="num">当前批次</th><th class="num">历史基线</th><th class="num">差异</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
    """


def _query_text(query: dict[str, str]) -> str:
    if not query:
        return "-"
    return json.dumps(query, ensure_ascii=False)


def _inline_detail(value: str, summary: str = "查看") -> str:
    """渲染可展开/收起的短字段。"""

    if not value or value == "-":
        return "-"
    if len(value) <= 28:
        return f'<span class="break-token">{_escape(value)}</span>'
    return (
        '<details class="inline-detail">'
        f"<summary>{_escape(summary)}</summary>"
        f'<code class="break-token">{_escape(value)}</code>'
        "</details>"
    )


def _body_detail(value: str) -> str:
    """渲染可展开/收起的响应体摘要。"""

    if not value:
        return "-"
    preview = _short_text(value.replace("\n", " "), 42)
    return (
        '<details class="body-detail">'
        f"<summary>{_escape(preview)}</summary>"
        f"<pre>{_escape(value)}</pre>"
        "</details>"
    )


def _failure_details_table(failure_details: list[FailureDetail], limit: int = 80) -> str:
    """渲染失败请求明细表。"""

    if not failure_details:
        return (
            '<div class="callout warn"><strong>暂无失败明细：</strong>'
            "当前报告未读取到 failure_details.jsonl。历史批次如未开启明细采集，"
            "只能查看 Locust failures CSV 中的聚合错误。</div>"
        )

    rows = []
    for index, detail in enumerate(failure_details[:limit], start=1):
        rows.append(
            f"""
            <tr class="fail-row">
              <td class="num">{index}</td>
              <td>{_escape(detail.method)}</td>
              <td class="url-cell">{_escape(detail.interface)}</td>
              <td><code>{_escape(_query_text(detail.query))}</code></td>
              <td class="reason-cell">{_escape(detail.failure_reason)}</td>
              <td>{_body_detail(detail.body_snippet)}</td>
            </tr>
            """
        )
    note = ""
    if len(failure_details) > limit:
        note = f'<p class="muted">仅展示前 {limit} 条，完整明细见原始 failure_details.jsonl。</p>'
    return f"""
    {note}
    <div class="details-wrap">
      <table class="details-table">
        <thead>
          <tr>
            <th class="num">#</th><th>方法</th><th>接口</th><th>入参</th><th>失败原因</th>
            <th>返回值</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _failure_samples(failure_details: list[FailureDetail], limit: int = 8) -> list[dict[str, object]]:
    """构造发给 LLM 的失败样例。"""

    samples = []
    for detail in failure_details[:limit]:
        samples.append(
            {
                "method": detail.method,
                "interface": detail.interface,
                "query": detail.query,
                "failure_reason": detail.failure_reason,
                "read_mode": detail.read_mode,
                "client_rt_ms": detail.client_rt_ms,
                "server_rt_ms": detail.server_rt_ms,
                "body_read_ms": detail.body_read_ms,
                "status_code": detail.status_code,
                "content_type": detail.content_type,
                "x_source": detail.response_x_source,
                "content_length": detail.content_length,
                "body_snippet": detail.body_snippet[:300],
            }
        )
    return samples


def _build_llm_messages(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail],
    image_summary: dict[str, object] | None = None,
    request_log_stats: RequestLogStats | None = None,
) -> list[dict[str, str]]:
    """构造 LLM 分析提示词。"""

    summary = summarize(rows)
    reason_counter = Counter(_failure_group_key(detail.failure_reason) for detail in failure_details)
    image_summary = image_summary or {}
    request_log_stats = request_log_stats or RequestLogStats()
    payload = {
        "scenario": context.scenario,
        "test_type": context.test_type_label,
        "plan": context.plan,
        "success_criteria": context.success_criteria,
        "extra_note": context.extra_note,
        "metrics": {
            "reqs_total_requests": summary["total_requests"],
            "fails_total_failures": summary["total_failures"],
            "median_p50_ms": max((row.p50_ms for row in rows), default=0.0),
            "req_per_second": summary["total_rps"],
            "total_requests": summary["total_requests"],
            "total_failures": summary["total_failures"],
            "error_rate": summary["error_rate"],
            "avg_ms": summary["avg_ms"],
            "max_p95_ms": summary["max_p95_ms"],
            "max_p99_ms": summary["max_p99_ms"],
            "max_ms": summary["max_ms"],
            "total_rps": summary["total_rps"],
        },
        "image_summary": image_summary,
        "request_log_check": {
            "record_count": request_log_stats.record_count,
            "response_x_source_javaapi": request_log_stats.response_x_source_javaapi,
            "avg_client_rt_ms": request_log_stats.avg_client_rt_ms,
            "avg_request_send_ms": request_log_stats.avg_request_send_ms,
            "avg_server_rt_ms": request_log_stats.avg_server_rt_ms,
            "server_rt_count": request_log_stats.server_rt_count,
            "avg_body_read_ms": request_log_stats.avg_body_read_ms,
            "avg_post_process_ms": request_log_stats.avg_post_process_ms,
            "avg_content_length": request_log_stats.avg_content_length,
            "read_mode_counts": request_log_stats.read_mode_counts or {},
        },
        "failure_reason_top": reason_counter.most_common(10),
        "failure_samples": _failure_samples(failure_details),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是资深性能测试分析师。请基于 Locust 指标和失败明细输出中文分析，"
                "要求结论可决策，不要复述所有原始数据，不要编造未提供的服务端指标。"
                "不要输出寒暄、免责声明、Markdown 分隔线。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请按以下结构输出：\n"
                "1. 结论判断\n"
                "2. 关键关注指标：必须覆盖 # reqs 总请求数、# fails 失败数、Median(P50 RT)、req/s 吞吐量、P95/P99、错误率\n"
                "3. 关键风险\n"
                "4. 性能问题定位清单：按“现象 / 可能原因 / 排查方向”给出，至少考虑 RT 随并发暴涨、TPS 到顶、5xx/非预期返回、P99 很高、错误率 > 1%\n"
                "5. 异常归因推测\n"
                "6. 排查建议\n"
                "7. 下一轮压测建议\n\n"
                f"数据如下：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
            ),
        },
    ]


def generate_llm_analysis(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail],
    enabled: bool,
    image_summary: dict[str, object] | None = None,
    request_log_stats: RequestLogStats | None = None,
    client=None,  # noqa: ANN001
) -> LlmAnalysis:
    """调用 AI-APIClient 生成辅助分析。"""

    if not enabled:
        return LlmAnalysis(text="", enabled=False)

    try:
        if client is None:
            from config.ai_apiclient import AiApiClient

            client = AiApiClient()
        text = client.call(
            _build_llm_messages(
                context,
                rows,
                failure_details,
                image_summary,
                request_log_stats,
            ),
            stream=True,
        )
        return LlmAnalysis(text=text.strip(), enabled=True)
    except Exception as exc:  # noqa: BLE001 - 报告生成不能被 LLM 失败阻断
        logger.warning("llm analysis failed error=%s", exc, exc_info=True)
        return LlmAnalysis(text="", enabled=True, error=str(exc))


def _llm_analysis_section(analysis: LlmAnalysis) -> str:
    """渲染 LLM 辅助分析区。"""

    if not analysis.enabled:
        return (
            '<div class="callout warn"><strong>未启用 LLM 分析：</strong>'
            "本报告仅展示 Locust 原始指标、图表和失败明细。</div>"
        )
    if analysis.error:
        return (
            '<div class="callout warn"><strong>LLM 分析失败：</strong>'
            f"{_escape(analysis.error)}。报告已保留原始指标和失败明细。</div>"
        )
    if not analysis.text:
        return (
            '<div class="callout warn"><strong>LLM 未返回内容：</strong>'
            "报告已保留原始指标和失败明细。</div>"
        )
    return f'<div class="llm-box">{_render_llm_markdown(analysis.text)}</div>'


def _format_inline_markdown(text: str) -> str:
    """格式化 LLM 文本中的少量 Markdown 行内样式。"""

    escaped = _escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _render_llm_markdown(text: str) -> str:
    """把 LLM Markdown 风格文本渲染成报告内的分析卡片。"""

    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if list_items:
            blocks.append(f"<ul>{''.join(list_items)}</ul>")
            list_items.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or set(line) <= {"-"}:
            continue
        if line.startswith("好的") or line.startswith("以下是"):
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", line)
        numbered_bullet_match = re.match(r"^\d+[.)]\s+(.+)$", line)
        numbered_text = numbered_bullet_match.group(1) if numbered_bullet_match else ""
        pure_bold_heading = bool(re.match(r"^\*\*[^*]+\*\*$", numbered_text))
        should_render_as_list = bool(
            bullet_match
            or (
                numbered_bullet_match
                and not pure_bold_heading
                and (
                    numbered_text.startswith("**")
                    or "：" in numbered_text
                    or ":" in numbered_text
                )
            )
        )
        if should_render_as_list:
            item = (bullet_match or numbered_bullet_match).group(1)
            list_items.append(f"<li>{_format_inline_markdown(item)}</li>")
            continue

        heading_match = re.match(r"^(?:#{1,4}\s*)?(?:\d+[.、]\s*)?(.+)$", line)
        is_heading = raw_line.startswith("#") or re.match(r"^\d+[.、]\s+", line)
        if is_heading and heading_match:
            flush_list()
            heading = heading_match.group(1).strip()
            blocks.append(f"<h3>{_format_inline_markdown(heading)}</h3>")
            continue

        flush_list()
        blocks.append(f"<p>{_format_inline_markdown(line)}</p>")

    flush_list()
    return "".join(blocks)


def render_html(
    context: ReportContext,
    rows: list[LocustStatsRow],
    failure_details: list[FailureDetail] | None = None,
    llm_analysis: LlmAnalysis | None = None,
    history_points: list[TrendPoint] | None = None,
    image_summary: dict[str, object] | None = None,
    request_log_stats: RequestLogStats | None = None,
    baseline_rows: list[LocustStatsRow] | None = None,
) -> str:
    """渲染 HTML 报告。"""

    failure_details = failure_details or []
    llm_analysis = llm_analysis or LlmAnalysis(text="", enabled=False)
    history_points = history_points or []
    image_summary = image_summary or {}
    request_log_stats = request_log_stats or RequestLogStats()
    summary = summarize(rows)
    status, badge_class, status_note = _status_badge(summary)
    callout_class = _callout_class(badge_class)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metric_explanations = "\n".join(
        f"<li><span class=\"metric-name\">{_escape(name)}</span>：{_escape(desc)}</li>"
        for name, desc in _metric_explanations(context)
    )
    display_api_name = _display_api_name(context, rows)
    display_method = _display_method(context, rows)
    display_host = _display_host(context, failure_details)
    display_path = _display_path(context, rows, failure_details)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(context.scenario)} 性能测试报告</title>
  <style>
    :root {{
      --paper: #F2EDE2;
      --paper-warm: #ECE5D5;
      --paper-soft: #F7F3EA;
      --ink: #1B1810;
      --ink-soft: #3A352B;
      --ink-muted: #6B6256;
      --rule: #C5BDA8;
      --rule-faint: #DDD5C0;
      --crit: #B73225;
      --crit-bg: rgba(183, 50, 37, 0.10);
      --warn: #BD7A18;
      --warn-bg: rgba(189, 122, 24, 0.12);
      --ok: #3F6B43;
      --ok-bg: rgba(63, 107, 67, 0.12);
      --mixed: #5C5B3F;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ background: var(--paper); }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 14.5px;
      line-height: 1.65;
      color: var(--ink);
      font-feature-settings: "tnum" 1;
    }}
    main {{ width: min(1180px, calc(100vw - 40px)); margin: 0 auto; padding: 36px 0 48px; }}
    .masthead {{ border-top: 3px solid var(--ink); border-bottom: 1px solid var(--rule); padding: 22px 0 20px; margin-bottom: 24px; }}
    .eyebrow, .section-kicker, .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: 0.04em; }}
    .eyebrow {{ color: var(--ink-muted); font-size: 11px; text-transform: uppercase; }}
    h1, h2, h3 {{ font-family: Georgia, "Times New Roman", "Songti SC", serif; font-weight: 500; color: var(--ink); margin: 0; }}
    h1 {{ font-size: clamp(32px, 5vw, 56px); line-height: 1.05; margin-top: 10px; letter-spacing: 0; }}
    h2 {{ font-size: 24px; line-height: 1.25; margin-bottom: 14px; }}
    .meta-line {{ display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 18px; color: var(--ink-soft); }}
    .stamp {{ display: inline-block; border: 1px solid var(--rule); background: var(--paper-warm); padding: 2px 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; color: var(--ink-soft); }}
    .section {{ border-top: 2px solid var(--ink); padding-top: 18px; margin-top: 30px; }}
    .section-kicker {{ color: var(--ink-muted); font-size: 11px; margin-bottom: 6px; }}
    .ledger-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1px solid var(--rule); border-left: 1px solid var(--rule); }}
    .ledger-cell {{ min-height: 96px; padding: 14px 16px; border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); background: rgba(247, 243, 234, 0.55); }}
    .ledger-label {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--ink-muted); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; }}
    .ledger-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 24px; line-height: 1.2; margin-top: 9px; color: var(--ink); word-break: break-word; }}
    .ledger-note {{ color: var(--ink-muted); font-size: 12px; margin-top: 6px; }}
    .badge {{ display: inline-block; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 10px; font-weight: 700; letter-spacing: 0.10em; padding: 4px 10px 5px; border: 1px solid transparent; }}
    .badge-pass {{ background: var(--ok); color: var(--paper); }}
    .badge-fail {{ background: var(--crit); color: var(--paper); }}
    .badge-warn {{ background: var(--warn); color: var(--paper); }}
    table {{ width: 100%; border-collapse: collapse; margin: 12px 0 18px; border-top: 1px solid var(--rule); border-left: 1px solid var(--rule); background: rgba(247, 243, 234, 0.52); }}
    th, td {{ border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 9px 10px; vertical-align: top; text-align: left; }}
    th {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; letter-spacing: 0.04em; color: var(--ink-soft); background: var(--paper-warm); }}
    td.num, th.num {{ text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: nowrap; }}
    tr.ok-row td {{ background: var(--ok-bg); }}
    tr.warn-row td {{ background: var(--warn-bg); }}
    tr.fail-row td {{ background: var(--crit-bg); }}
    .two-col {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; }}
    .pane {{ position: relative; border: 1px solid var(--rule); background: var(--paper-soft); padding: 18px 18px 16px; }}
    .pane::before {{ content: attr(data-label); position: absolute; top: -10px; left: 12px; background: var(--paper); padding: 0 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 10px; color: var(--ink-muted); letter-spacing: 0.08em; }}
    .callout {{ border: 1px solid var(--rule); border-left: 4px solid var(--mixed); background: var(--paper-soft); padding: 14px 16px; margin: 12px 0; }}
    .callout.ok {{ border-left-color: var(--ok); }}
    .callout.warn {{ border-left-color: var(--warn); }}
    .callout.fail {{ border-left-color: var(--crit); }}
    .explain-list {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; margin: 0; padding: 0; list-style: none; }}
    .explain-list li {{ border-bottom: 1px dashed var(--rule-faint); padding-bottom: 8px; }}
    .metric-name {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--ink); font-weight: 700; }}
    .muted {{ color: var(--ink-muted); font-size: 12px; }}
    .interpret-panel {{ margin-top: 18px; border: 1px solid var(--rule); background: var(--paper-soft); padding: 16px; }}
    .interpret-panel h3 {{ font-size: 18px; margin-bottom: 12px; }}
    .interpret-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .interpret-card {{ border: 1px solid var(--rule-faint); background: var(--paper); padding: 12px; min-height: 120px; }}
    .interpret-name {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; color: var(--ink-soft); }}
    .interpret-value {{ font-size: 22px; font-weight: 800; margin: 6px 0; color: var(--ink); }}
    .interpret-card p {{ font-size: 12px; color: var(--ink-muted); margin: 0; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 12px 0 10px; }}
    .metric-card {{ position: relative; min-height: 150px; border: 1px solid var(--rule); border-top: 3px solid var(--mixed); background: linear-gradient(180deg, rgba(247, 243, 234, 0.96), rgba(236, 229, 213, 0.54)); padding: 14px 14px 13px; }}
    .metric-card::after {{ content: ""; position: absolute; left: 14px; right: 14px; bottom: 44px; border-bottom: 1px dashed var(--rule-faint); }}
    .metric-card-label {{ display: block; min-height: 34px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.03em; color: var(--ink-soft); overflow-wrap: anywhere; }}
    .metric-card strong {{ display: block; margin: 10px 0 18px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: clamp(20px, 2.4vw, 30px); line-height: 1.05; color: var(--ink); overflow-wrap: anywhere; }}
    .metric-card small {{ display: block; color: var(--ink-muted); font-size: 12px; line-height: 1.45; }}
    .metric-card:nth-child(1) {{ border-top-color: var(--crit); }}
    .metric-card:nth-child(2) {{ border-top-color: var(--ok); }}
    .metric-card:nth-child(3) {{ border-top-color: var(--warn); }}
    .metric-card:nth-child(4) {{ border-top-color: var(--mixed); }}
    .metric-card:nth-child(5) {{ border-top-color: var(--crit); }}
    .metric-card:nth-child(6) {{ border-top-color: var(--ok); }}
    .read-mode-strip {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid var(--rule); background: var(--paper-soft); padding: 10px 12px; margin: 0 0 18px; }}
    .read-mode-strip > span {{ flex: 0 0 auto; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; font-weight: 700; color: var(--ink-soft); letter-spacing: 0.04em; }}
    .read-mode-strip > div {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; min-width: 0; }}
    .read-mode-pill {{ display: inline-flex; align-items: baseline; gap: 8px; border: 1px solid var(--rule-faint); background: var(--paper); padding: 4px 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; color: var(--ink-muted); }}
    .read-mode-pill strong {{ color: var(--ink); font-size: 11px; }}
    .chart-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
    .chart-box {{ border: 1px solid var(--rule); background: var(--paper-soft); padding: 16px; min-height: 220px; }}
    .chart-box h3 {{ font-size: 18px; margin-bottom: 12px; }}
    .chart-row {{ display: grid; grid-template-columns: 118px minmax(80px, 1fr) 64px; align-items: center; gap: 8px; margin: 10px 0; }}
    .chart-label, .chart-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; color: var(--ink-soft); }}
    .chart-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .chart-value {{ text-align: right; }}
    .chart-track {{ height: 16px; border: 1px solid var(--rule); background: var(--paper-warm); overflow: hidden; }}
    .chart-track span {{ display: block; height: 100%; max-width: 100%; background: var(--crit); }}
    .trend-svg {{ width: 100%; height: 150px; }}
    .trend-svg line {{ stroke: var(--rule); stroke-width: 1; }}
    .trend-svg polyline {{ fill: none; stroke: var(--crit); stroke-width: 3; }}
    .trend-dot {{ fill: var(--paper-soft); stroke: var(--crit); stroke-width: 2; pointer-events: none; }}
    .trend-dot-hit {{ fill: transparent; stroke: transparent; cursor: crosshair; pointer-events: all; }}
    .trend-point:hover .trend-dot {{ fill: var(--crit); r: 4.5; }}
    .trend-tooltip {{ display: none; pointer-events: none; }}
    .trend-tooltip rect {{ fill: var(--paper); stroke: var(--ink); stroke-width: 1; filter: drop-shadow(0 4px 8px rgba(0, 0, 0, 0.18)); }}
    .trend-tooltip text {{ fill: var(--ink); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 9px; }}
    .trend-point:hover .trend-tooltip {{ display: block; }}
    .topology, .heatmap {{ display: grid; gap: 10px; align-items: stretch; margin: 12px 0; }}
    .topology {{ grid-template-columns: 1fr 32px 1fr 32px 1.4fr; }}
    .top-node, .heat-cell {{ border: 1px solid var(--rule); background: var(--paper-soft); padding: 16px; min-height: 82px; }}
    .top-node span, .heat-cell span {{ display: block; margin-top: 6px; color: var(--ink-muted); font-size: 12px; }}
    .top-node.hot, .heat-cell.hot {{ border-left: 5px solid var(--crit); background: var(--crit-bg); }}
    .top-node.ok, .heat-cell.cool {{ border-left: 5px solid var(--ok); background: var(--ok-bg); }}
    .heat-cell.warm {{ border-left: 5px solid var(--warn); background: var(--warn-bg); }}
    .top-arrow {{ display: grid; place-items: center; color: var(--ink-muted); font-size: 22px; }}
    .heatmap {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .empty-chart {{ color: var(--ink-muted); font-size: 12px; padding: 24px 0; }}
    .details-table {{ table-layout: fixed; min-width: 780px; }}
    .details-table th:nth-child(1), .details-table td:nth-child(1) {{ width: 48px; }}
    .details-table th:nth-child(2), .details-table td:nth-child(2) {{ width: 70px; }}
    .details-table th:nth-child(3), .details-table td:nth-child(3) {{ width: 26%; }}
    .details-table th:nth-child(4), .details-table td:nth-child(4) {{ width: 110px; }}
    .details-table th:nth-child(5), .details-table td:nth-child(5) {{ width: 32%; }}
    .details-table th:nth-child(6), .details-table td:nth-child(6) {{ width: 24%; }}
    .details-wrap {{ width: 100%; overflow-x: auto; border-left: 1px solid var(--rule); border-right: 1px solid var(--rule); }}
    .url-cell, .reason-cell, pre, code, .break-token, .break-cell {{ overflow-wrap: anywhere; word-break: break-word; white-space: normal; }}
    .report-collapse {{ border: 1px solid var(--rule); background: var(--paper-soft); margin: 12px 0 18px; }}
    .report-collapse > summary {{ cursor: pointer; padding: 10px 14px; background: var(--paper-warm); color: var(--ink-soft); border-bottom: 1px solid var(--rule); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; letter-spacing: 0.04em; }}
    .report-collapse:not([open]) > summary {{ border-bottom: 0; }}
    .report-collapse .callout {{ margin: 14px; }}
    .report-collapse .details-wrap {{ border-left: 0; border-right: 0; }}
    .report-collapse > .llm-box {{ border: 0; }}
    .inline-detail summary, .body-detail summary {{ cursor: pointer; color: var(--crit); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; }}
    .inline-detail code {{ display: block; margin-top: 6px; border: 1px solid var(--rule-faint); background: rgba(236, 229, 213, 0.7); padding: 5px; }}
    .body-detail pre {{ margin-top: 8px; }}
    pre {{ margin: 0; max-height: 180px; overflow: auto; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; white-space: pre-wrap; }}
    .llm-box {{ border: 1px solid var(--rule); background: var(--paper-soft); padding: 0; overflow: hidden; }}
    .llm-box h3 {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 16px; font-weight: 700; margin: 0; padding: 12px 16px; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule-faint); background: var(--paper-warm); }}
    .llm-box h3:first-child {{ border-top: 0; }}
    .llm-box p {{ margin: 0; padding: 10px 16px; border-bottom: 1px dashed var(--rule-faint); }}
    .llm-box ul {{ margin: 0; padding: 10px 20px 12px 34px; border-bottom: 1px dashed var(--rule-faint); }}
    .llm-box li {{ margin: 6px 0; }}
    .llm-box strong {{ color: var(--crit); }}
    .llm-box code {{ display: inline; border: 1px solid var(--rule-faint); background: rgba(236, 229, 213, 0.72); padding: 1px 4px; }}
    .footer {{ margin-top: 34px; border-top: 1px solid var(--rule); padding-top: 12px; color: var(--ink-muted); font-size: 12px; }}
    @media (max-width: 980px) {{
      main {{ width: min(100vw - 24px, 1180px); }}
      .ledger-grid, .two-col, .explain-list, .chart-grid, .interpret-grid, .metric-grid, .topology, .heatmap {{ grid-template-columns: 1fr; }}
      .read-mode-strip {{ align-items: flex-start; flex-direction: column; }}
      .read-mode-strip > div {{ justify-content: flex-start; }}
      .top-arrow {{ display: none; }}
    }}
  </style>
</head>
<body>
<main>
  <header class="masthead">
    <div class="eyebrow">LOCUST PERFORMANCE LEDGER · 中文压测报告</div>
    <h1>{_escape(context.scenario)} 性能测试报告</h1>
    <div class="meta-line">
      <span class="stamp">报告编号：LOCUST-REPORT</span>
      <span class="stamp">生成时间：{_escape(generated_at)}</span>
      <span class="stamp">执行环境：{_escape(context.environment)}</span>
      <span class="stamp">测试类型：{_escape(context.test_type_label)}</span>
      <span class="stamp">结论：{_escape(status)}</span>
    </div>
  </header>

  <section class="section">
    <div class="section-kicker">01 · 测试概览</div>
    <h2>时间、环境、工具版本</h2>
    <div class="ledger-grid">{_metric_cards(context, rows)}</div>
    <table>
      <tbody>
        <tr><th>报告生成时间</th><td>{_escape(generated_at)}</td><th>执行环境</th><td>{_escape(context.environment)}</td></tr>
        <tr><th>压测工具</th><td>Locust {_escape(_package_version("locust"))}</td><th>Python</th><td>{_escape(platform.python_version())}</td></tr>
        <tr><th>数据文件</th><td class="mono">{_escape(context.data_file)}</td><th>执行人</th><td>{_escape(context.executor)}</td></tr>
      </tbody>
    </table>
    <div class="callout {callout_class}">
      <strong>本次判定：</strong>{_escape(status_note)}
    </div>
  </section>

  <section class="section">
    <div class="section-kicker">02 · 测试方案</div>
    <h2>测试目标与执行方案</h2>
    <div class="two-col">
      <div class="pane" data-label="测试概要">
        <table><tbody>
          <tr><th>场景名称</th><td>{_escape(context.scenario)}</td></tr>
          <tr><th>测试类型</th><td>{_escape(context.test_type_label)}</td></tr>
          <tr><th>测试概要</th><td>{_escape(context.overview)}</td></tr>
          <tr><th>执行人</th><td>{_escape(context.executor)}</td></tr>
        </tbody></table>
      </div>
      <div class="pane" data-label="测试方案设计">
        <table><tbody>
          <tr><th>方案说明</th><td>{_escape(context.plan)}</td></tr>
          <tr><th>成功判定</th><td>{_escape(context.success_criteria)}</td></tr>
          <tr><th>观察指标</th><td>{_escape(_focus_metrics_text(context))}</td></tr>
          <tr><th>补充统计</th><td>{_escape(context.extra_note or "-")}</td></tr>
        </tbody></table>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="section-kicker">03 · 指标总览与趋势</div>
    <h2>TPS、RT、错误率趋势</h2>
    {_trend_section(history_points)}
    {_metric_interpretation(rows)}
    <h2>请求级耗时拆分</h2>
    {_request_timing_breakdown(request_log_stats)}
  </section>

  <section class="section">
    <div class="section-kicker">04 · 参数设置与测试数据</div>
    <h2>运行参数</h2>
    <div class="two-col">
      <div class="pane" data-label="Locust 参数">
        <table><tbody>
          <tr><th>并发用户数</th><td class="mono">{_escape(context.users)}</td></tr>
          <tr><th>启动速率</th><td class="mono">{_escape(context.spawn_rate)}</td></tr>
          <tr><th>压测时长</th><td class="mono">{_escape(context.run_time)}</td></tr>
          <tr><th>执行环境</th><td>{_escape(context.environment)}</td></tr>
        </tbody></table>
      </div>
      <div class="pane" data-label="测试数据">
        <table><tbody>
          <tr><th>数据文件</th><td class="mono">{_escape(context.data_file)}</td></tr>
          <tr><th>数据说明</th><td>测试数据独立存放，报告仅记录路径和用途，不展示敏感内容。</td></tr>
        </tbody></table>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="section-kicker">05 · 被测接口信息</div>
    <h2>接口范围</h2>
    <div class="pane" data-label="接口信息">
      <table><tbody>
        <tr><th>接口名称</th><td>{_escape(display_api_name)}</td></tr>
        <tr><th>请求方法</th><td class="mono">{_escape(display_method)}</td></tr>
        <tr><th>目标域名</th><td class="mono">{_escape(display_host)}</td></tr>
        <tr><th>请求路径</th><td class="mono">{_escape(display_path)}</td></tr>
      </tbody></table>
    </div>
  </section>

  <section class="section">
    <div class="section-kicker">06 · 指标说明</div>
    <h2>{_escape(context.test_type_label)} 关注指标</h2>
    <ul class="explain-list">{metric_explanations}</ul>
  </section>

  <section class="section">
    <div class="section-kicker">07 · 可视化展示</div>
    <h2>趋势图、瓶颈高亮图、调用链热图</h2>
    {_charts(rows, failure_details)}
    <h2>瓶颈高亮图</h2>
    {_topology_highlight(context, rows, failure_details)}
    <h2>请求级调用链热图</h2>
    {_call_chain_heatmap(context, rows, failure_details, image_summary, request_log_stats)}
  </section>

  <section class="section">
    <div class="section-kicker">08 · 瓶颈分析</div>
    <h2>问题点、影响程度、关联指标</h2>
    {_bottleneck_analysis(context, rows, failure_details)}
  </section>

  <section class="section">
    <div class="section-kicker">09 · 原因定位</div>
    <h2>详细指标与日志分析</h2>
    {_root_cause_table(failure_details)}
  </section>

  <section class="section">
    <div class="section-kicker">10 · Locust 统计汇总</div>
    <h2>接口明细</h2>
    <table>
      <thead>
        <tr>
          <th>方法</th><th>接口</th><th class="num">请求数</th><th class="num">失败数</th><th class="num">失败率</th>
          <th class="num">平均(ms)</th><th class="num">P95(ms)</th><th class="num">P99(ms)</th><th class="num">RPS</th>
        </tr>
      </thead>
      <tbody>{_interface_table(rows)}</tbody>
    </table>
  </section>

  <section class="section">
    <div class="section-kicker">11 · 报错明细</div>
    <h2>失败请求：接口、入参、返回</h2>
    <details class="report-collapse">
      <summary>展开/收起报错明细（默认收起，最多展示前 80 条）</summary>
      {_failure_details_table(failure_details)}
    </details>
  </section>

  <section class="section">
    <div class="section-kicker">12 · LLM 辅助分析</div>
    <h2>AI 分析总结</h2>
    <details class="report-collapse" open>
      <summary>展开/收起 AI 分析总结（默认展开）</summary>
      {_llm_analysis_section(llm_analysis)}
    </details>
  </section>

  <section class="section">
    <div class="section-kicker">13 · 优化建议与优先级</div>
    <h2>优化建议</h2>
    <div class="callout {callout_class}">
      <strong>{_escape(status)}：</strong>{_escape(status_note)}
    </div>
    {_optimization_table(rows, failure_details)}
  </section>

  <section class="section">
    <div class="section-kicker">14 · 历史对比</div>
    <h2>历史基线对比</h2>
    {_history_comparison_section(rows, baseline_rows)}
  </section>

  <div class="footer">模板版本：v1.1 · 风格：台账风 Ledger Style · 生成方式：tools.report_builder · 数据来源：Locust stats CSV</div>
</main>
</body>
</html>
"""


def _focus_metrics_text(context: ReportContext) -> str:
    if context.test_type in BASELINE_TYPES or context.test_type_label in BASELINE_TYPES:
        return "平均响应、P95、P99、失败率、吞吐量"
    return "吞吐量、失败率、P95/P99、最大响应、最慢接口"


def build_report(
    context: ReportContext,
    stats_csv: Path,
    current_timestamp: str | None = None,
    failure_details_file: Path | None = None,
    history_file: Path | None = None,
    image_summary_file: Path | None = None,
    request_log_file: Path | None = None,
    baseline_stats_csv: Path | None = None,
    enable_llm_analysis: bool = False,
    llm_client=None,  # noqa: ANN001
) -> Path:
    """生成 HTML 报告并返回输出路径。"""

    logger.info(
        "report build started scenario=%s test_type=%s stats_csv=%s",
        context.scenario,
        context.test_type,
        stats_csv,
    )
    rows = parse_locust_stats(stats_csv)
    failure_details = parse_failure_details(failure_details_file)
    history_points = parse_history(history_file)
    image_summary = parse_image_summary(image_summary_file)
    request_log_stats = parse_request_log_stats(request_log_file)
    baseline_rows = parse_locust_stats(baseline_stats_csv) if baseline_stats_csv else None
    llm_analysis = generate_llm_analysis(
        context,
        rows,
        failure_details,
        enabled=enable_llm_analysis,
        image_summary=image_summary,
        request_log_stats=request_log_stats,
        client=llm_client,
    )
    paths = build_report_paths(context.scenario, context.test_type, current_timestamp=current_timestamp)
    paths.html_report.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html(
        context,
        rows,
        failure_details,
        llm_analysis,
        history_points,
        image_summary,
        request_log_stats,
        baseline_rows,
    )
    paths.html_report.write_text(html_text, encoding="utf-8")
    logger.info("report build finished html_report=%s", paths.html_report)
    return paths.html_report


def _build_context_from_args(args: argparse.Namespace) -> ReportContext:
    return ReportContext(
        scenario=args.scenario,
        test_type=args.test_type,
        overview=args.overview,
        plan=args.plan,
        environment=args.environment,
        executor=args.executor,
        users=args.users,
        spawn_rate=args.spawn_rate,
        run_time=args.run_time,
        host=args.host,
        data_file=args.data_file,
        api_name=args.api_name,
        method=args.method,
        path=args.path,
        success_criteria=args.success_criteria,
        extra_note=args.extra_note,
    )


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(description="Build HTML report from Locust stats CSV.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--test-type", required=True)
    parser.add_argument("--stats-csv", required=True, type=Path)
    parser.add_argument("--overview", default="待补充：本次压测的业务背景与测试目的。")
    parser.add_argument("--plan", default="待补充：并发模型、执行阶段、观察窗口和退出条件。")
    parser.add_argument("--environment", default="test")
    parser.add_argument("--executor", default="QA")
    parser.add_argument("--users", default="-")
    parser.add_argument("--spawn-rate", default="-")
    parser.add_argument("--run-time", default="-")
    parser.add_argument("--host", default="-")
    parser.add_argument("--data-file", default="-")
    parser.add_argument("--api-name", default="-")
    parser.add_argument("--method", default="-")
    parser.add_argument("--path", default="-")
    parser.add_argument("--success-criteria", default="HTTP 成功 + 业务断言通过 + 失败率在阈值内。")
    parser.add_argument("--extra-note", default="")
    parser.add_argument("--report-timestamp", default=None)
    parser.add_argument("--failure-details-file", default=None, type=Path)
    parser.add_argument("--history-file", default=None, type=Path)
    parser.add_argument("--image-summary-file", default=None, type=Path)
    parser.add_argument("--request-log-file", default=None, type=Path)
    parser.add_argument("--baseline-stats-csv", default=None, type=Path)
    parser.add_argument("--enable-llm-analysis", action="store_true")
    args = parser.parse_args()
    output = build_report(
        _build_context_from_args(args),
        args.stats_csv,
        current_timestamp=args.report_timestamp,
        failure_details_file=args.failure_details_file,
        history_file=args.history_file,
        image_summary_file=args.image_summary_file,
        request_log_file=args.request_log_file,
        baseline_stats_csv=args.baseline_stats_csv,
        enable_llm_analysis=args.enable_llm_analysis,
    )
    print(output)


if __name__ == "__main__":
    main()
