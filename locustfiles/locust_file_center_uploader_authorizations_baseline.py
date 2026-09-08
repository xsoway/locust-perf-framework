#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文件中心临时密钥接口基准测试脚本。

命名格式：locust_<业务域>_<接口或场景>_<测试类型>.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any

from locust import HttpUser, between, events, task

from tools.config_loader import load_runtime_config
from tools.logging_setup import get_logger

runtime_config = load_runtime_config()

DEFAULT_HOST = "https://example.com"
DEFAULT_PATH = "/file-center-api/open/bucket/uploaderAuthorizations"
DEFAULT_DATA_FILE = Path("data/examples/uploader_authorizations_baseline.json")
SENSITIVE_HEADER_NAMES = {"appkey", "authorization", "cookie", "token"}


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
    """按需获取临时密钥接口压测脚本 logger。"""

    return get_logger("locust_file_center_uploader_authorizations_baseline", runtime_config.log_dir)


def load_request_case(data_file: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """从 data 目录读取请求头和请求体。"""

    if not data_file.exists():
        raise FileNotFoundError(f"临时密钥接口数据文件不存在: {data_file}")

    raw_data = json.loads(data_file.read_text(encoding="utf-8"))
    headers = raw_data.get("headers", {})
    payload = raw_data.get("payload", {})
    if not isinstance(headers, dict) or not isinstance(payload, dict):
        raise ValueError("临时密钥接口数据文件必须包含 headers 和 payload 对象")

    return (
        {str(key): str(value) for key, value in headers.items()},
        dict(payload),
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
        return f"临时密钥接口 HTTP 状态异常 status={response.status_code}"

    try:
        response_json = response.json()
    except ValueError:
        return ""

    if is_business_success(response_json):
        return ""

    return (
        "临时密钥接口业务失败 "
        f"code={response_json.get('code', '-')} "
        f"message={response_json.get('message', '-')}"
    )


def build_failure_detail(response, reason: str, elapsed_ms: float) -> dict[str, object]:  # noqa: ANN001
    """构造失败请求明细，供中文报告展示原因定位。"""

    return {
        "method": "POST",
        "interface": request_path,
        "url": f"{target_host.rstrip('/')}{request_path}",
        "query": {},
        "request_headers": redact_headers(request_headers),
        "request_body": request_payload,
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


request_data_file = Path(os.getenv("LOCUST_UPLOADER_AUTHORIZATIONS_DATA_FILE", str(DEFAULT_DATA_FILE)))
request_headers, request_payload = load_request_case(request_data_file)
request_path = os.getenv("LOCUST_UPLOADER_AUTHORIZATIONS_PATH", DEFAULT_PATH)
target_host = os.getenv(
    "LOCUST_UPLOADER_AUTHORIZATIONS_HOST",
    os.getenv("LOCUST_TARGET_HOST", runtime_config.host if runtime_config.host != "http://localhost" else DEFAULT_HOST),
)
failure_detail_writer = JsonlWriter(os.getenv("LOCUST_UPLOADER_AUTHORIZATIONS_FAILURE_DETAILS_FILE"))


class UploaderAuthorizationsBaselineUser(HttpUser):
    """文件中心临时密钥接口基准测试用户。"""

    host = target_host
    wait_time = between(
        runtime_config.wait_time_min_seconds,
        runtime_config.wait_time_max_seconds,
    )

    @task
    def post_uploader_authorizations(self) -> None:
        """POST 临时密钥接口，并校验 HTTP 状态和业务成功字段。"""

        # 接口名称固定，保证 Locust CSV/HTML 中的统计行稳定可对比。
        started_at = time.perf_counter()
        with self.client.post(
            request_path,
            name="POST uploader_authorizations",
            headers=request_headers,
            json=request_payload,
            timeout=runtime_config.request_timeout_seconds,
            catch_response=True,
        ) as response:
            reason = failure_reason(response)
            if reason:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                failure_detail_writer.write(
                    build_failure_detail(response, reason, elapsed_ms)
                )
                get_script_logger().warning(
                    "uploader_authorizations_failed reason=%s response=%s",
                    reason,
                    response_text_snippet(response),
                )
                response.failure(reason)
                return

            response.success()


@events.init.add_listener
def log_uploader_authorizations_start(environment, **kwargs) -> None:  # noqa: ANN001
    """Locust 初始化时输出接口和数据文件信息。"""

    get_script_logger().info(
        "uploader authorizations script loaded host=%s path=%s data_file=%s headers=%s",
        target_host,
        request_path,
        request_data_file,
        redact_headers(request_headers),
    )
