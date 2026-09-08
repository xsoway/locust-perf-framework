#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时密钥接口压测脚本测试。"""

from __future__ import annotations

import json
from pathlib import Path

from locustfiles.locust_file_center_uploader_authorizations_baseline import (
    failure_reason,
    load_request_case,
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


def test_load_request_case_reads_headers_and_payload(tmp_path: Path) -> None:
    """可以从 data JSON 读取请求头和请求体。"""

    data_file = tmp_path / "case.json"
    data_file.write_text(
        json.dumps(
            {
                "headers": {"appId": "test-app", "random": 333},
                "payload": {"userId": 100000001},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    headers, payload = load_request_case(data_file)

    assert headers == {"appId": "test-app", "random": "333"}
    assert payload == {"userId": 100000001}


def test_redact_headers_masks_app_key() -> None:
    """日志输出会脱敏 appKey。"""

    headers = redact_headers({"appId": "test-app", "appKey": "secret"})

    assert headers == {"appId": "test-app", "appKey": "***REDACTED***"}


def test_failure_reason_detects_business_failure() -> None:
    """HTTP 200 但业务 success=false 时应判定失败。"""

    reason = failure_reason(FakeResponse(200, {"code": 400, "message": "失败", "success": False}))

    assert "业务失败" in reason
    assert "code=400" in reason


def test_failure_reason_accepts_success_response() -> None:
    """HTTP 200 且业务成功时应判定成功。"""

    reason = failure_reason(FakeResponse(200, {"code": 0, "message": "ok", "success": True}))

    assert reason == ""
