#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time     : 2026/08/27 16:00
# @Filename : locust_op_pay_out_return_stress.py
# @Author   : Alan_Hsu
"""运营后台 payOutReturn 支付回调压力测试脚本。

场景说明：用户A 发送 requestId 1001，用户B 发送 requestId 1002，两个用户
同时进行、各执行一次后自动停止，压测随即退出，验证并发退款回调场景下接口的
稳定性与业务处理结果。

数据来源：data/examples/pay_out_return_stress.json（两条脱敏示例请求体）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any

import gevent
from locust import HttpUser, constant, events, task
from locust.user.users import StopUser

from tools.config_loader import load_runtime_config
from tools.logging_setup import get_logger

runtime_config = load_runtime_config()

DEFAULT_HOST = "https://example.com"
DEFAULT_PATH = "/op-manage-inner-api/op/assets/payOutReturn"
DEFAULT_DATA_FILE = Path("data/examples/pay_out_return_stress.json")
SENSITIVE_HEADER_NAMES = {"appkey", "authorization", "cookie", "token"}

# 每条请求的统计名称，方便 Locust CSV/HTML 中区分两个 requestId 场景。
NAME_BY_REQUEST_ID = {
    1001: "POST payOutReturn requestId=1001 示例用户A",
    1002: "POST payOutReturn requestId=1002 示例用户B",
}


class JsonlWriter:
    """线程安全 JSONL 写入器。"""

    def __init__(self, output_file: str | None) -> None:
        self.lock = Lock()
        self.output_file = Path(output_file) if output_file else None
        if self.output_file:
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            self.output_file.write_text("", encoding="utf-8")

    def write(self, detail: dict[str, object]) -> None:
        """追加一条 JSON 记录。"""

        if not self.output_file:
            return
        with self.lock:
            with self.output_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(detail, ensure_ascii=False) + "\n")


def get_script_logger():
    """按需获取 payOutReturn 压力测试脚本 logger。"""

    return get_logger("locust_op_pay_out_return_stress", runtime_config.log_dir)


def load_request_cases(data_file: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """从 data 目录读取请求头和全部请求体。"""

    if not data_file.exists():
        raise FileNotFoundError(f"payOutReturn 数据文件不存在: {data_file}")

    raw_data = json.loads(data_file.read_text(encoding="utf-8"))
    headers = raw_data.get("headers", {})
    payloads = raw_data.get("payloads", [])
    if not isinstance(headers, dict):
        raise ValueError("payOutReturn 数据文件必须包含 headers 对象")
    if not isinstance(payloads, list) or not payloads:
        raise ValueError("payOutReturn 数据文件必须包含非空 payloads 列表")

    return (
        {str(key): str(value) for key, value in headers.items()},
        [dict(payload) for payload in payloads if isinstance(payload, dict)],
    )


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """对日志中的敏感请求头做脱敏。"""

    return {
        key: "***REDACTED***" if key.lower() in SENSITIVE_HEADER_NAMES else value
        for key, value in headers.items()
    }


def response_text_snippet(response, limit: int = 500) -> str:  # noqa: ANN001
    """提取响应体片段，避免日志写入过长内容。"""

    return (response.text or "")[:limit]


def response_headers(response) -> dict[str, str]:  # noqa: ANN001
    """提取响应头，便于后续排查。"""

    return {str(key): str(value) for key, value in dict(response.headers).items()}


def is_business_success(response_json: object) -> bool:
    """判断常见 JSON 响应体是否为业务成功。"""

    if not isinstance(response_json, dict):
        return True

    success_value = response_json.get("success")
    if success_value is False:
        return False

    code_value = response_json.get("code")
    if code_value is None:
        return True
    return code_value in (0, 200, "0", "200")


def failure_reason(response) -> str:  # noqa: ANN001
    """根据 HTTP 状态和 JSON 业务字段生成失败原因。"""

    if response.status_code != 200:
        return f"payOutReturn HTTP 状态异常 status={response.status_code}"

    try:
        response_json = response.json()
    except ValueError:
        return ""

    if is_business_success(response_json):
        return ""

    return (
        "payOutReturn 业务失败 "
        f"code={response_json.get('code', '-')} "
        f"message={response_json.get('message', '-')}"
    )


def build_failure_detail(response, reason: str, elapsed_ms: float, payload: dict[str, Any]) -> dict[str, object]:  # noqa: ANN001
    """构造失败请求明细，供中文报告展示原因定位。"""

    return {
        "method": "POST",
        "interface": request_path,
        "url": f"{target_host.rstrip('/')}{request_path}",
        "query": {"requestId": payload.get("requestId")},
        "request_headers": redact_headers(request_headers),
        "request_body": payload,
        "failure_reason": reason,
        "elapsed_ms": round(elapsed_ms, 2),
        "response": {
            "status_code": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
            "x_source": response.headers.get("x-source", ""),
            "headers": response_headers(response),
            "content_length": len(response.content or b""),
            "body_snippet": response_text_snippet(response),
        },
    }


request_data_file = Path(os.getenv("LOCUST_PAY_OUT_RETURN_DATA_FILE", str(DEFAULT_DATA_FILE)))
request_headers, request_payloads = load_request_cases(request_data_file)
request_path = os.getenv("LOCUST_PAY_OUT_RETURN_PATH", DEFAULT_PATH)
target_host = os.getenv("LOCUST_PAY_OUT_RETURN_HOST", DEFAULT_HOST)
failure_detail_writer = JsonlWriter(os.getenv("LOCUST_PAY_OUT_RETURN_FAILURE_DETAILS_FILE"))


def _post_pay_out_return_once(user: HttpUser, payload: dict[str, Any]) -> None:
    """发送单条 payOutReturn 请求，校验 HTTP 状态和业务成功字段。"""

    request_id = payload.get("requestId")
    request_name = NAME_BY_REQUEST_ID.get(request_id, f"POST payOutReturn requestId={request_id}")
    started_at = time.perf_counter()
    with user.client.post(
        request_path,
        name=request_name,
        headers=request_headers,
        json=payload,
        timeout=runtime_config.request_timeout_seconds,
        catch_response=True,
    ) as response:
        reason = failure_reason(response)
        if reason:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            failure_detail_writer.write(
                build_failure_detail(response, reason, elapsed_ms, payload)
            )
            get_script_logger().warning(
                "pay_out_return_failed request_id=%s reason=%s response=%s",
                request_id,
                reason,
                response_text_snippet(response),
            )
            response.failure(reason)
            return

        response.success()


def _watch_for_all_users_done(environment, initial_user_count: int) -> None:  # noqa: ANN001
    """后台监控：全部用户完成一轮停止后主动结束压测。"""

    # 用户完成请求后 StopUser 会异步停止，轮询等待 user_count 归零。
    while True:
        gevent.sleep(1)
        runner = environment.runner
        if runner is None:
            return
        if runner.user_count <= 0:
            runner.quit()
            return


class PayOutReturnUserA(HttpUser):
    """用户A：发送 requestId=1001，一次后自动停止。"""

    host = target_host
    # 思考等待时间为 0：用户启动后立即发送请求。
    wait_time = constant(0)

    @task
    def post_pay_out_return(self) -> None:
        """用户A 只发送第一条请求（requestId=1001）。"""

        _post_pay_out_return_once(self, request_payloads[0])
        raise StopUser()


class PayOutReturnUserB(HttpUser):
    """用户B：发送 requestId=1002，一次后自动停止。"""

    host = target_host
    # 思考等待时间为 0：用户启动后立即发送请求。
    wait_time = constant(0)

    @task
    def post_pay_out_return(self) -> None:
        """用户B 只发送第二条请求（requestId=1002）。"""

        _post_pay_out_return_once(self, request_payloads[1])
        raise StopUser()


@events.quitting.add_listener
def log_pay_out_return_quitting(environment, **kwargs) -> None:  # noqa: ANN001
    """压测结束时输出执行摘要。"""

    stats = environment.stats
    get_script_logger().info(
        "pay_out_return run finished total_requests=%s total_failures=%s fail_ratio=%.4f",
        stats.total.num_requests,
        stats.total.num_failures,
        stats.total.fail_ratio,
    )


@events.init.add_listener
def log_pay_out_return_start(environment, **kwargs) -> None:  # noqa: ANN001
    """Locust 初始化时输出接口信息，并启动退出监控。"""

    get_script_logger().info(
        "pay_out_return script loaded host=%s path=%s data_file=%s payload_count=%s headers=%s",
        target_host,
        request_path,
        request_data_file,
        len(request_payloads),
        redact_headers(request_headers),
    )

    # 每个用户只执行一次，全部用户停止后主动结束压测，无需等待 -t 窗口。
    if environment.runner is not None:
        gevent.spawn(_watch_for_all_users_done, environment, environment.runner.user_count)