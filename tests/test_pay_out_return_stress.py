#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""payOutReturn 压力测试脚本测试。"""

from __future__ import annotations

import json
from pathlib import Path

from locustfiles.locust_op_pay_out_return_stress import (
    NAME_BY_REQUEST_ID,
    failure_reason,
    load_request_cases,
    redact_headers,
)


class FakeResponse:
    """测试用响应对象。"""

    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)

    def json(self) -> object:
        """返回测试响应 JSON。"""

        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_load_request_cases_reads_headers_and_payloads(tmp_path: Path) -> None:
    """可以从 data JSON 读取请求头和全部请求体。"""

    data_file = tmp_path / "case.json"
    data_file.write_text(
        json.dumps(
            {
                "headers": {"Content-Type": "application/json"},
                "payloads": [
                    {"requestId": 1001, "memberId": 100000001},
                    {"requestId": 1002, "memberId": 100000001},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    headers, payloads = load_request_cases(data_file)

    assert headers == {"Content-Type": "application/json"}
    assert [p["requestId"] for p in payloads] == [1001, 1002]


def test_load_request_cases_rejects_empty_payloads(tmp_path: Path) -> None:
    """payloads 为空时应抛出异常。"""

    data_file = tmp_path / "empty.json"
    data_file.write_text(json.dumps({"headers": {}, "payloads": []}), encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="payloads"):
        load_request_cases(data_file)


def test_redact_headers_masks_sensitive_keys() -> None:
    """日志输出会脱敏 token/appKey 等敏感请求头。"""

    headers = redact_headers({"Content-Type": "application/json", "Token": "secret-token"})

    assert headers == {"Content-Type": "application/json", "Token": "***REDACTED***"}


def test_failure_reason_detects_business_failure() -> None:
    """HTTP 200 但业务 success=false 时应判定失败。"""

    reason = failure_reason(FakeResponse(200, {"code": 500, "message": "处理失败", "success": False}))

    assert "业务失败" in reason
    assert "code=500" in reason


def test_failure_reason_accepts_success_response() -> None:
    """HTTP 200 且业务成功时应判定成功。"""

    reason = failure_reason(FakeResponse(200, {"code": 0, "message": "ok", "success": True}))

    assert reason == ""


def test_failure_reason_flags_http_error() -> None:
    """HTTP 非 200 时应判定失败。"""

    reason = failure_reason(FakeResponse(500, {"message": "server error"}))

    assert "HTTP 状态异常" in reason
    assert "status=500" in reason


def test_request_name_mapping_covers_both_request_ids() -> None:
    """两条业务请求都有稳定的 Locust 统计名称。"""

    assert 1001 in NAME_BY_REQUEST_ID
    assert 1002 in NAME_BY_REQUEST_ID
    assert "1001" in NAME_BY_REQUEST_ID[1001]
    assert "1002" in NAME_BY_REQUEST_ID[1002]
