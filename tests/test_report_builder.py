#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报告生成器测试。"""

from __future__ import annotations

from pathlib import Path

from tools.report_builder import (
    build_report,
    LlmAnalysis,
    parse_failure_details,
    parse_history,
    parse_image_summary,
    parse_locust_stats,
    parse_request_log_stats,
    render_html,
    summarize,
)
from tools.report_builder import ReportContext


def test_parse_locust_stats(tmp_path: Path) -> None:
    """可以解析 Locust stats CSV。"""

    csv_file = tmp_path / "stats_stats.csv"
    csv_file.write_text(
        "Type,Name,Request Count,Failure Count,Average Response Time,Min Response Time,"
        "Max Response Time,50%,95%,99%,Requests/s\n"
        "GET,/health,10,1,20,10,40,18,35,39,2.5\n",
        encoding="utf-8",
    )

    rows = parse_locust_stats(csv_file)
    summary = summarize(rows)

    assert len(rows) == 1
    assert rows[0].name == "/health"
    assert summary["total_requests"] == 10
    assert summary["error_rate"] == 0.1


def test_parse_history(tmp_path: Path) -> None:
    """可以解析 Locust history 趋势 CSV。"""

    history_file = tmp_path / "stats_stats_history.csv"
    history_file.write_text(
        "Timestamp,User Count,Type,Name,Requests/s,Failures/s,95%,"
        "Total Request Count,Total Failure Count,Total Average Response Time\n"
        "1,10,,Aggregated,20,1,300,100,5,120\n",
        encoding="utf-8",
    )

    points = parse_history(history_file)

    assert len(points) == 1
    assert points[0].rps == 20
    assert points[0].p95_ms == 300


def test_render_trend_chart_with_hover_tooltips(tmp_path: Path) -> None:
    """指标趋势图会渲染可悬停的数据点。"""

    rows = parse_locust_stats(
        _write_stats_csv(tmp_path / "stats_stats.csv", request_count=10, failure_count=1)
    )
    html = render_html(
        ReportContext(
            scenario="image_url",
            test_type="stress",
            overview="图片 URL 压测",
            plan="20 并发覆盖 URL",
            environment="test",
            executor="QA",
            users="20",
            spawn_rate="5 user/s",
            run_time="覆盖完成自动停止",
            host="example.test",
            data_file="data/02-url.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="/image/a.jpg",
            success_criteria="HTTP 200 且响应体可识别为图片内容",
        ),
        rows,
        [],
        history_points=[
            _trend_point("1719210000", 5, 10.5, 200, 100, 1),
            _trend_point("1719210001", 10, 12.5, 300, 200, 2),
        ],
    )

    assert "trend-dot-hit" in html
    assert "trend-tooltip" in html
    assert "TPS / RPS 趋势" in html
    assert "时间: 1719210000" in html
    assert "当前值: 10.5 req/s" in html
    assert "P95: 200 ms" in html
    assert "平均RT: 100 ms" in html
    assert "累计请求: 100" in html


def test_render_metric_interpretation_for_readability(tmp_path: Path) -> None:
    """报告会用通俗语言解释 P50、吞吐量、P95 和 P99。"""

    rows = parse_locust_stats(
        _write_stats_csv(
            tmp_path / "stats_stats.csv",
            request_count=100,
            failure_count=0,
            p50=1200,
            p95=3000,
            p99=3800,
            rps=37.5,
        )
    )
    html = render_html(
        ReportContext(
            scenario="image_url",
            test_type="stress",
            overview="图片 URL 压测",
            plan="100 并发覆盖 URL",
            environment="test",
            executor="QA",
            users="100",
            spawn_rate="5 user/s",
            run_time="覆盖完成自动停止",
            host="example.test",
            data_file="data/02-url.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="/image/a.jpg",
            success_criteria="图片响应断言通过",
        ),
        rows,
        [],
    )

    assert "关键指标怎么读" in html
    assert "Median(P50 RT)" in html
    assert "50% 的请求响应时间小于等于 1200 ms" in html
    assert "系统平均每秒处理 37.5 个请求" in html
    assert "约 5% 的请求会更慢" in html
    assert "最慢 1% 请求的尾部延迟" in html
    assert "综合解读" in html


def test_parse_request_log_stats_counts_response_x_source(tmp_path: Path) -> None:
    """可以从全量请求日志复算 x-source=javaapi。"""

    request_log = tmp_path / "request_details.jsonl"
    request_log.write_text(
        '{"response":{"x_source":"javaapi"}}\n'
        '{"response":{"x_source":"JAVAAPI"}}\n'
        '{"response":{"x_source":""}}\n',
        encoding="utf-8",
    )

    stats = parse_request_log_stats(request_log)

    assert stats.record_count == 3
    assert stats.response_x_source_javaapi == 2
    assert stats.avg_client_rt_ms == 0.0


def test_parse_request_log_stats_aggregates_timing_metrics(tmp_path: Path) -> None:
    """全量请求日志可以聚合请求级耗时拆分指标。"""

    request_log = tmp_path / "request_details.jsonl"
    request_log.write_text(
        '{"read_mode":"first_chunk","client_rt_ms":100,"request_send_ms":70,'
        '"server_rt_ms":30,"body_read_ms":10,"post_process_ms":20,'
        '"content_length":1024,"response":{"x_source":"javaapi"}}\n'
        '{"read_mode":"full","client_rt_ms":300,"request_send_ms":180,'
        '"server_rt_ms":"","body_read_ms":80,"post_process_ms":40,'
        '"content_length":4096,"response":{"x_source":""}}\n',
        encoding="utf-8",
    )

    stats = parse_request_log_stats(request_log)

    assert stats.record_count == 2
    assert stats.avg_client_rt_ms == 200.0
    assert stats.avg_request_send_ms == 125.0
    assert stats.avg_server_rt_ms == 30.0
    assert stats.server_rt_count == 1
    assert stats.avg_body_read_ms == 45.0
    assert stats.avg_post_process_ms == 30.0
    assert stats.avg_content_length == 2560.0
    assert stats.read_mode_counts == {"first_chunk": 1, "full": 1}


def test_parse_failure_details_tolerates_non_jsonl_csv(tmp_path: Path) -> None:
    """误传非 JSONL 文件（如 Locust 原生 stats_failures.csv）时不应崩溃，应降级返回空。"""

    csv_file = tmp_path / "stats_failures.csv"
    csv_file.write_text(
        "Method,Name,Error,Occurrences,First Seen,Last Seen\n"
        'GET,GET /health,ConnectionError,10,2026-09-10 09:00:00,2026-09-10 09:01:00\n',
        encoding="utf-8",
    )

    details = parse_failure_details(csv_file)

    assert details == []


def test_parse_failure_details_tolerates_malformed_lines(tmp_path: Path) -> None:
    """合法 JSONL 中混入坏行时只跳过坏行，其余正常解析，不应抛异常。"""

    details_file = tmp_path / "failure_details.jsonl"
    details_file.write_text(
        '{"method":"GET","interface":"https://example.test/a","url":"https://example.test/a",'
        '"failure_reason":"ok",'
        '"response":{"status_code":500}}\n'
        'this is not valid json\n'
        '{"method":"GET","interface":"https://example.test/b","url":"https://example.test/b",'
        '"failure_reason":"also ok",'
        '"response":{"status_code":503}}\n',
        encoding="utf-8",
    )

    details = parse_failure_details(details_file)

    assert len(details) == 2
    assert details[0].url == "https://example.test/a"
    assert details[1].url == "https://example.test/b"


def test_parse_failure_details_expands_status_zero_error(tmp_path: Path) -> None:
    """旧失败明细中 status=0 时应展示底层连接异常。"""

    details_file = tmp_path / "failure_details.jsonl"
    details_file.write_text(
        '{"method":"GET","interface":"https://example.test/a.jpg","url":"https://example.test/a.jpg",'
        '"failure_reason":"status错误 status=0",'
        '"error":{"type":"ConnectionError","category":"connection_error","message":"Failed to resolve host"},'
        '"response":{"status_code":0,"content_length":0,"body_snippet":""}}\n',
        encoding="utf-8",
    )

    details = parse_failure_details(details_file)

    assert details[0].failure_reason.startswith(
        "请求异常 category=connection_error type=ConnectionError"
    )
    assert "Failed to resolve host" in details[0].failure_reason


def test_render_report_groups_status_zero_errors_by_category(tmp_path: Path) -> None:
    """失败类型分布应按异常类别聚合，不应被 URL 打散。"""

    details_file = tmp_path / "failure_details.jsonl"
    details_file.write_text(
        '{"method":"GET","interface":"https://example.test/a.jpg","url":"https://example.test/a.jpg",'
        '"failure_reason":"status错误 status=0",'
        '"error":{"type":"ConnectionError","category":"connection_error","message":"Failed to resolve a"},'
        '"response":{"status_code":0,"content_length":0,"body_snippet":""}}\n'
        '{"method":"GET","interface":"https://example.test/b.jpg","url":"https://example.test/b.jpg",'
        '"failure_reason":"status错误 status=0",'
        '"error":{"type":"ConnectionError","category":"connection_error","message":"Failed to resolve b"},'
        '"response":{"status_code":0,"content_length":0,"body_snippet":""}}\n',
        encoding="utf-8",
    )
    rows = parse_locust_stats(
        _write_stats_csv(tmp_path / "stats_stats.csv", request_count=2, failure_count=2)
    )
    html = render_html(
        ReportContext(
            scenario="image_url",
            test_type="stress",
            overview="图片 URL 压测",
            plan="覆盖 URL",
            environment="test",
            executor="QA",
            users="2",
            spawn_rate="1 user/s",
            run_time="覆盖完成自动停止",
            host="URL 文件内完整地址",
            data_file="data/examples/url_sample.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="URL 文件内图片地址",
            success_criteria="HTTP 200",
        ),
        rows,
        parse_failure_details(details_file),
    )

    assert "请求异常 / connection_error / ConnectionError" in html
    assert "<td class=\"num\">2</td>" in html


def test_render_report_with_failure_details(tmp_path: Path) -> None:
    """中文报告展示失败请求的接口、入参和返回摘要。"""

    details_file = tmp_path / "failure_details.jsonl"
    details_file.write_text(
        '{"method":"GET","interface":"https://example.com/a.jpg","url":"https://example.com/a.jpg?id=1",'
        '"query":{"id":"1"},"request_headers":{},"failure_reason":"图片内容校验失败",'
        '"response":{"status_code":200,"content_type":"application/json","x_source":"javaapi",'
        '"content_length":89,"body_snippet":"{\\"code\\":500,\\"msg\\":\\"not image\\"}"}}\n',
        encoding="utf-8",
    )
    rows = parse_locust_stats(
        _write_stats_csv(tmp_path / "stats_stats.csv", request_count=1, failure_count=1)
    )
    details = parse_failure_details(details_file)
    html = render_html(
        ReportContext(
            scenario="image_url",
            test_type="stress",
            overview="图片 URL 压测",
            plan="100 并发覆盖 2000 条 URL",
            environment="test",
            executor="QA",
            users="100",
            spawn_rate="5 user/s",
            run_time="覆盖完成自动停止",
            host="URL 文件内完整地址",
            data_file="data/examples/url_sample.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="URL 文件内图片地址",
            success_criteria="HTTP 200 且响应体可识别为图片内容",
        ),
        rows,
        details,
        image_summary={"response_x_source_javaapi": 1},
        request_log_stats=parse_request_log_stats(_write_request_log(tmp_path / "request.jsonl")),
    )

    assert "可视化展示" in html
    assert "瓶颈高亮图" in html
    assert "失败请求：接口、入参、返回" in html
    assert "report-collapse" in html
    assert "展开/收起报错明细" in html
    assert "reason-cell" in html
    assert 'title="图片内容校验失败"' in html
    assert "x-source=javaapi：1 次" in html
    assert "请求日志与 summary 一致" in html
    assert "请求级耗时拆分" in html
    assert "metric-card" in html
    assert "read-mode-pill" in html
    assert "client_rt_ms 平均" in html
    assert "request_send_ms 平均" in html
    assert "post_process_ms 平均" in html
    assert "read_mode 分布" in html
    assert "content_length" in html
    assert "<th>返回值</th>" in html
    assert '<th class="num">状态码</th>' not in html
    assert '<th class="num">client_rt_ms</th>' not in html
    assert '<th class="num">server_rt_ms</th>' not in html
    assert '<th class="num">body_read_ms</th>' not in html
    assert '<th class="num">content_length</th>' not in html
    assert "<th>read_mode</th>" not in html
    assert "<th>返回内容摘要</th>" not in html
    assert "<th>Content-Type</th>" not in html
    assert "<th>x-source</th>" not in html
    assert "返回字节" not in html
    assert "https://example.com/a.jpg" in html
    assert "{&quot;id&quot;: &quot;1&quot;}" in html
    assert "not image" in html


def test_render_report_uses_dynamic_scope_and_generic_topology(tmp_path: Path) -> None:
    """报告拓扑和接口范围不写死图片场景。"""

    details_file = tmp_path / "failure_details.jsonl"
    details_file.write_text(
        '{"method":"POST","interface":"https://api.example.test/order/create",'
        '"url":"https://api.example.test/order/create?channel=h5",'
        '"query":{"channel":"h5"},"failure_reason":"接口请求失败 status=500",'
        '"response":{"status_code":500,"body_snippet":"server error"}}\n',
        encoding="utf-8",
    )
    rows = parse_locust_stats(
        _write_stats_csv(tmp_path / "stats_stats.csv", request_count=10, failure_count=1)
    )
    details = parse_failure_details(details_file)

    html = render_html(
        ReportContext(
            scenario="order_create",
            test_type="stress",
            overview="订单创建压测",
            plan="30 并发覆盖订单创建请求",
            environment="test",
            executor="QA",
            users="30",
            spawn_rate="10 user/s",
            run_time="5m",
            host="-",
            data_file="data/order_create.csv",
            api_name="订单创建",
            method="POST",
            path="-",
            success_criteria="HTTP 200 且业务成功",
        ),
        rows,
        details,
    )

    assert "api.example.test" in html
    assert "/order/create" in html
    assert "30 并发 / 10 user/s 启动" in html
    assert "订单创建 请求链路" in html
    assert "图片 URL 入口" not in html
    assert "图片服务 / CDN / 对象存储" not in html
    assert "URL 分配" not in html
    assert "图片校验" not in html


def test_build_report_with_fake_llm_analysis(tmp_path: Path) -> None:
    """启用 LLM 分析时，报告写入模型返回的分析总结。"""

    stats_csv = _write_stats_csv(tmp_path / "stats_stats.csv", request_count=10, failure_count=2)
    details_file = tmp_path / "failure_details.jsonl"
    details_file.write_text("", encoding="utf-8")

    class FakeClient:
        def call(self, messages, stream=True):  # noqa: ANN001
            assert messages
            assert stream is True
            return "结论判断：失败率偏高，需要排查图片服务返回。"

    report = build_report(
        ReportContext(
            scenario="image_url_llm_test",
            test_type="stress",
            overview="图片 URL 压测",
            plan="100 并发覆盖 2000 条 URL",
            environment="test",
            executor="QA",
            users="100",
            spawn_rate="5 user/s",
            run_time="覆盖完成自动停止",
            host="URL 文件内完整地址",
            data_file="data/examples/url_sample.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="URL 文件内图片地址",
            success_criteria="HTTP 200 且响应体可识别为图片内容",
        ),
        stats_csv,
        current_timestamp="20260624_123456",
        failure_details_file=details_file,
        enable_llm_analysis=True,
        llm_client=FakeClient(),
    )

    html = report.read_text(encoding="utf-8")
    assert "AI 分析总结" in html
    assert "展开/收起 AI 分析总结" in html
    assert "失败率偏高" in html
    report.unlink()


def test_build_report_with_baseline_and_summary_files(tmp_path: Path) -> None:
    """传入历史基线和辅助统计后，报告展示对比与响应头复核。"""

    stats_csv = _write_stats_csv(tmp_path / "stats_stats.csv", request_count=10, failure_count=2)
    baseline_csv = _write_stats_csv(
        tmp_path / "baseline_stats.csv", request_count=10, failure_count=0
    )
    summary_file = tmp_path / "summary.json"
    summary_file.write_text('{"response_x_source_javaapi": 1}', encoding="utf-8")
    request_log = _write_request_log(tmp_path / "request.jsonl")

    report = build_report(
        ReportContext(
            scenario="image_url_baseline_test",
            test_type="stress",
            overview="图片 URL 压测",
            plan="100 并发覆盖 2000 条 URL",
            environment="test",
            executor="QA",
            users="100",
            spawn_rate="5 user/s",
            run_time="覆盖完成自动停止",
            host="URL 文件内完整地址",
            data_file="data/examples/url_sample.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="URL 文件内图片地址",
            success_criteria="HTTP 200 且响应体可识别为图片内容",
        ),
        stats_csv,
        current_timestamp="20260624_123457",
        image_summary_file=summary_file,
        request_log_file=request_log,
        baseline_stats_csv=baseline_csv,
    )

    html = report.read_text(encoding="utf-8")
    assert parse_image_summary(summary_file)["response_x_source_javaapi"] == 1
    assert "历史基线" in html
    assert "当前批次" in html
    assert "请求日志与 summary 一致" in html
    report.unlink()


def test_render_llm_analysis_as_structured_html(tmp_path: Path) -> None:
    """LLM Markdown 文本会被渲染成结构化 HTML。"""

    rows = parse_locust_stats(
        _write_stats_csv(tmp_path / "stats_stats.csv", request_count=10, failure_count=2)
    )
    html = render_html(
        ReportContext(
            scenario="image_url",
            test_type="stress",
            overview="图片 URL 压测",
            plan="100 并发覆盖 2000 条 URL",
            environment="test",
            executor="QA",
            users="100",
            spawn_rate="5 user/s",
            run_time="覆盖完成自动停止",
            host="URL 文件内完整地址",
            data_file="data/examples/url_sample.txt",
            api_name="图片 URL 加载",
            method="GET",
            path="URL 文件内图片地址",
            success_criteria="HTTP 200 且响应体可识别为图片内容",
        ),
        rows,
        [],
        LlmAnalysis(
            text=(
                "### 1. 结论判断\n"
                "**未通过**\n"
                "- 失败率偏高\n"
                "2. **关键关注指标**\n"
                "1. **排查日志**：检查 `x-source`"
            ),
            enabled=True,
        ),
    )

    assert "### 1." not in html
    assert "<h3>结论判断</h3>" in html
    assert "<strong>未通过</strong>" in html
    assert "<li>失败率偏高</li>" in html
    assert "<h3><strong>关键关注指标</strong></h3>" in html
    assert "<li><strong>排查日志</strong>：检查 <code>x-source</code></li>" in html


def _write_stats_csv(
    path: Path,
    request_count: int,
    failure_count: int,
    p50: float = 18,
    p95: float = 35,
    p99: float = 39,
    rps: float = 2.5,
) -> Path:
    """写入测试用 Locust stats CSV。"""

    path.write_text(
        "Type,Name,Request Count,Failure Count,Average Response Time,Min Response Time,"
        "Max Response Time,50%,95%,99%,Requests/s\n"
        f"GET,GET image_url,{request_count},{failure_count},20,10,40,{p50},{p95},{p99},{rps}\n",
        encoding="utf-8",
    )
    return path


def _write_request_log(path: Path) -> Path:
    """写入测试用全量请求日志。"""

    path.write_text(
        '{"read_mode":"first_chunk","client_rt_ms":100,"request_send_ms":80,'
        '"server_rt_ms":25,"body_read_ms":8,"post_process_ms":12,'
        '"content_length":1024,"response":{"x_source":"javaapi"}}\n',
        encoding="utf-8",
    )
    return path


def _trend_point(
    timestamp: str,
    user_count: int,
    rps: float,
    p95_ms: float,
    total_requests: int,
    total_failures: int,
):
    """构造测试用趋势点。"""

    from tools.report_builder import TrendPoint

    return TrendPoint(
        timestamp=timestamp,
        user_count=user_count,
        rps=rps,
        failures_per_second=0.0,
        p95_ms=p95_ms,
        avg_ms=100.0,
        total_requests=total_requests,
        total_failures=total_failures,
    )
