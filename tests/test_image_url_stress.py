#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图片 URL 压测脚本测试。"""

from __future__ import annotations

from pathlib import Path

from locustfiles import locust_image_url_stress as image_url_stress
from locustfiles.locust_image_url_stress import (
    ImageDownloadRecorder,
    assert_image_response,
    body_read_failure_reason,
    build_failure_detail,
    build_request_log,
    fetch_image_with_urllib,
    is_incomplete_read_failure,
    read_urllib_exact,
    read_stream_content,
    request_failure_reason,
    response_error_detail,
    should_retry_incomplete_read,
)


class FakeResponse:
    """测试用响应对象。"""

    status_code = 0
    headers = {}
    content = None
    error = None
    request = None


class FakeErrorResponse(FakeResponse):
    """测试用异常响应对象。"""

    error = image_url_stress.requests.exceptions.ReadTimeout("read timed out")


class FakeIncompleteReadResponse(FakeResponse):
    """测试用响应体未读完整异常。"""

    error = image_url_stress.requests.exceptions.ChunkedEncodingError(
        "('Connection broken: IncompleteRead(42067 bytes read, 152485 more expected)', "
        "IncompleteRead(42067 bytes read, 152485 more expected))"
    )


class FakeSuccessResponse(FakeResponse):
    """测试用成功图片响应对象。"""

    status_code = 200
    headers = {
        "Content-Type": "image/jpeg",
        "Content-Length": "14",
        "x-source": "javaapi",
    }
    content = b"\xff\xd8\xfffake-image"


class FakeTextResponse(FakeResponse):
    """测试用非图片响应对象。"""

    status_code = 200
    headers = {"Content-Type": "text/html", "Content-Length": "12"}
    content = b"<html></html>"


class FakeBadLengthResponse(FakeResponse):
    """测试用非法 Content-Length 响应对象。"""

    status_code = 200
    headers = {"Content-Type": "image/jpeg", "Content-Length": "abc"}
    content = b"\xff\xd8\xfffake-image"


class FakeStreamSuccessResponse(FakeResponse):
    """测试用流式成功响应对象。"""

    status_code = 200
    headers = {"Content-Type": "image/jpeg", "Content-Length": "13"}
    content = b""

    def iter_content(self, chunk_size):  # noqa: ANN001
        """模拟 requests 分块读取。"""

        yield b"\xff\xd8\xfffake"
        yield b"-image"


class FakeStreamIncompleteResponse(FakeResponse):
    """测试用流式读取中断响应对象。"""

    status_code = 200
    headers = {"Content-Type": "image/jpeg", "Content-Length": "194552"}
    content = b""

    def iter_content(self, chunk_size):  # noqa: ANN001
        """模拟 requests 在读取响应体时抛出 IncompleteRead。"""

        yield b"\xff\xd8\xffpartial"
        raise image_url_stress.requests.exceptions.ChunkedEncodingError(
            "('Connection broken: IncompleteRead(70547 bytes read, 224356 more expected)', "
            "IncompleteRead(70547 bytes read, 224356 more expected))"
        )


class FakeStreamShortResponse(FakeResponse):
    """测试用响应体短于 Content-Length 的响应对象。"""

    status_code = 200
    headers = {"Content-Type": "image/jpeg", "Content-Length": "100"}
    content = b""

    def iter_content(self, chunk_size):  # noqa: ANN001
        """模拟连接正常结束但响应体长度不足。"""

        yield b"\xff\xd8\xffshort"


class FakeUrllibResponse:
    """测试用 urllib 响应对象。"""

    def __init__(self, content: bytes, length: int | None = None) -> None:
        self.content = content
        self.length = length
        self.offset = 0
        self.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": str(len(content)),
            "x-source": "javaapi",
        }

    def __enter__(self):
        """进入上下文。"""

        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        """退出上下文。"""

    def getcode(self) -> int:
        """返回 HTTP 状态码。"""

        return 200

    def read(self, size: int | None = None) -> bytes:
        """模拟 urllib 按指定长度读取响应体。"""

        if size is None:
            size = len(self.content) - self.offset
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeUrllibPartialResponse(FakeUrllibResponse):
    """测试用单次 read 返回不足但可继续读取的响应对象。"""

    def read(self, size: int | None = None) -> bytes:
        """模拟 read(n) 先返回部分内容，后续继续返回。"""

        if self.offset == 0 and size and size > 5:
            size = 5
        return super().read(size)


class FakeUrllibIncompleteResponse(FakeUrllibResponse):
    """测试用 urllib 响应体未读完整对象。"""

    def read(self, size: int | None = None) -> bytes:
        """模拟 http.client.IncompleteRead。"""

        raise image_url_stress.http.client.IncompleteRead(b"\xff\xd8\xffpartial", 90)


def test_build_failure_detail_handles_empty_content() -> None:
    """连接失败无响应体时也能生成失败明细。"""

    detail = build_failure_detail(
        "http://127.0.0.1:1/no-image.jpg?case=smoke",
        FakeResponse(),
        "图片请求失败 status=0",
    )

    assert detail["query"] == {"case": "smoke"}
    assert detail["response"]["content_length"] == 0
    assert detail["response"]["body_snippet"] == ""


def test_status_zero_failure_reason_includes_exception_detail() -> None:
    """status=0 时失败原因应包含具体异常类型。"""

    reason = request_failure_reason(FakeErrorResponse())
    error = response_error_detail(FakeErrorResponse())
    log = build_request_log(
        "http://127.0.0.1:1/no-image.jpg",
        FakeErrorResponse(),
        12.3,
        False,
        reason,
    )

    assert "category=read_timeout" in reason
    assert "type=ReadTimeout" in reason
    assert error["category"] == "read_timeout"
    assert log["error"]["type"] == "ReadTimeout"


def test_request_log_includes_client_server_and_body_metrics() -> None:
    """请求明细应记录客户端耗时、服务端耗时、读 body 耗时和图片大小。"""

    response = FakeSuccessResponse()
    response.headers = {
        **FakeSuccessResponse.headers,
        "X-Runtime": "0.123",
    }
    response._client_rt_ms = 456.78
    response._request_send_ms = 400.0
    response._body_read_ms = 12.34
    response._post_process_ms = 44.44

    log = build_request_log(
        "http://example.test/images/a.jpg",
        response,
        500.0,
        True,
        "",
    )

    assert log["client_rt_ms"] == 456.78
    assert log["request_send_ms"] == 400.0
    assert log["server_rt_ms"] == 123.0
    assert log["body_read_ms"] == 12.34
    assert log["post_process_ms"] == 44.44
    assert log["content_length"] == len(FakeSuccessResponse.content)
    assert log["read_mode"] == image_url_stress.IMAGE_READ_MODE


def test_sync_locust_request_meta_uses_full_client_rt() -> None:
    """Locust 上报 RT 应覆盖为包含 body 读取后的完整客户端耗时。"""

    response = FakeSuccessResponse()
    response.request_meta = {
        "response_time": 12.0,
        "response_length": 0,
    }

    image_url_stress.sync_locust_request_meta(response, 345.67)

    assert response.request_meta["response_time"] == 345.67
    assert response.request_meta["response_length"] == len(FakeSuccessResponse.content)


def test_server_rt_can_parse_server_timing_header() -> None:
    """服务端耗时应支持从 Server-Timing 提取。"""

    response = FakeSuccessResponse()
    response.headers = {
        "Content-Type": "image/jpeg",
        "Server-Timing": "cdn-cache; desc=MISS, app;dur=45.6",
    }

    assert image_url_stress.response_server_rt_ms(response) == 45.6


def test_chunked_encoding_error_is_classified_as_incomplete_read() -> None:
    """响应体中途断开时应提取已读和期望字节数。"""

    reason = request_failure_reason(FakeIncompleteReadResponse())
    error = response_error_detail(FakeIncompleteReadResponse())

    assert "category=incomplete_read" in reason
    assert "type=ChunkedEncodingError" in reason
    assert error["category"] == "incomplete_read"
    assert error["bytes_read"] == 42067
    assert error["bytes_expected_remaining"] == 152485
    assert error["bytes_expected_total"] == 194552


def test_http_status_failure_keeps_real_status_code() -> None:
    """404/500 等 HTTP 响应应保留真实状态码。"""

    class Fake404Response(FakeResponse):
        status_code = 404

    assert request_failure_reason(Fake404Response()) == "图片请求失败 status=404"


def test_assert_image_response_uses_expected_rules() -> None:
    """图片断言应按状态码、响应头、响应体和完整下载规则判断。"""

    assert assert_image_response(FakeSuccessResponse(), FakeSuccessResponse.content) == ""
    assert (
        assert_image_response(FakeTextResponse(), FakeTextResponse.content)
        == "Content-Type错误 content_type=text/html"
    )
    assert (
        assert_image_response(FakeBadLengthResponse(), FakeBadLengthResponse.content)
        == "Content-Length不是数字 content_length=abc"
    )
    assert assert_image_response(FakeSuccessResponse(), b"") == "响应体为空"
    assert (
        assert_image_response(FakeStreamShortResponse(), b"\xff\xd8\xffshort", True)
        == "图片未完整下载 expected=100 actual=8"
    )


def test_image_response_failure_reason_expands_status_zero_error() -> None:
    """status=0 时图片失败原因应展示底层请求异常。"""

    reason = image_url_stress.image_response_failure_reason(FakeErrorResponse(), b"")

    assert "category=read_timeout" in reason
    assert "type=ReadTimeout" in reason


def test_read_stream_content_success_uses_iter_content() -> None:
    """图片响应体应通过 iter_content 分块读取完成。"""

    response = FakeStreamSuccessResponse()
    content, failure_reason = read_stream_content(response)

    assert failure_reason == ""
    assert content == b"\xff\xd8\xfffake-image"
    assert response._image_content == content


def test_read_stream_content_incomplete_read_has_detailed_reason() -> None:
    """流式读取中断时应保留 IncompleteRead 的详细原因。"""

    response = FakeStreamIncompleteResponse()
    content, failure_reason = read_stream_content(response)
    error = response_error_detail(response)

    assert content == b"\xff\xd8\xffpartial"
    assert "category=incomplete_read" in failure_reason
    assert "type=ChunkedEncodingError" in failure_reason
    assert error["bytes_read"] == 70547
    assert error["bytes_expected_remaining"] == 224356
    assert is_incomplete_read_failure(response, failure_reason) is True
    assert should_retry_incomplete_read(response, failure_reason, attempt=1) is False


def test_read_stream_content_short_body_has_structured_error() -> None:
    """读取长度小于 Content-Length 时应写入结构化错误。"""

    response = FakeStreamShortResponse()
    content, failure_reason = read_stream_content(response)
    error = response_error_detail(response)

    assert content == b"\xff\xd8\xffshort"
    assert "图片流式读取未完整" in failure_reason
    assert error["category"] == "stream_incomplete_read"
    assert error["bytes_read"] == 8
    assert error["bytes_expected_remaining"] == 92
    assert is_incomplete_read_failure(response, failure_reason) is True


def test_fetch_image_with_urllib_reads_response_length(monkeypatch) -> None:
    """urllib 模式应优先使用 resp.length 读取响应体。"""

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeUrllibResponse(b"\xff\xd8\xffurllib-image", length=15)

    monkeypatch.setattr(image_url_stress.urllib.request, "urlopen", fake_urlopen)

    response = fetch_image_with_urllib("http://example.test/images/a.jpg")

    assert response.status_code == 200
    response_body = image_url_stress.response_body_bytes(response)
    assert response_body
    assert response_body == b"\xff\xd8\xffurllib-image"
    assert response.headers["x-source"] == "javaapi"


def test_fetch_image_with_urllib_loops_until_expected_length(monkeypatch) -> None:
    """urllib 模式应循环读取，直到达到 expected length。"""

    response = FakeUrllibPartialResponse(b"\xff\xd8\xffurllib-image", length=15)

    assert read_urllib_exact(response, 15) == b"\xff\xd8\xffurllib-image"


def test_fetch_image_with_urllib_records_incomplete_read(monkeypatch) -> None:
    """urllib 模式遇到 IncompleteRead 时应保留 partial bytes 和错误明细。"""

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeUrllibIncompleteResponse(b"", length=100)

    monkeypatch.setattr(image_url_stress.urllib.request, "urlopen", fake_urlopen)

    response = fetch_image_with_urllib("http://example.test/images/a.jpg")
    failure_reason = body_read_failure_reason(response)
    error = response_error_detail(response)

    assert response.status_code == 200
    assert image_url_stress.response_body_bytes(response) == b"\xff\xd8\xffpartial"
    assert "category=incomplete_read" in failure_reason
    assert error["bytes_read"] == 10
    assert error["bytes_expected_remaining"] == 90


def test_image_download_recorder_saves_file_and_success_log(monkeypatch, tmp_path) -> None:
    """成功图片应保存到本地目录并写入下载成功日志。"""

    monkeypatch.setattr(image_url_stress, "IMAGE_READ_MODE", "full")
    download_dir = tmp_path / "downloaded_images"
    download_log = tmp_path / "download_results.jsonl"
    recorder = ImageDownloadRecorder(str(download_dir), str(download_log))
    response = FakeSuccessResponse()
    response._client_rt_ms = 88.8
    response._request_send_ms = 70.0
    response._body_read_ms = 6.6
    response._post_process_ms = 12.2

    record = recorder.record_success(
        "http://example.test/images/a.jpg?version=1",
        response,
    )

    saved_file = Path(record["file_path"])
    assert record["download_success"] is True
    assert record["client_rt_ms"] == 88.8
    assert record["request_send_ms"] == 70.0
    assert record["body_read_ms"] == 6.6
    assert record["post_process_ms"] == 12.2
    assert record["content_length"] == len(FakeSuccessResponse.content)
    assert record["read_mode"] == "full"
    assert saved_file.exists()
    assert saved_file.read_bytes() == FakeSuccessResponse.content
    assert saved_file.name.startswith("a_")
    assert '"download_success": true' in download_log.read_text(encoding="utf-8")


def test_image_download_recorder_writes_failure_reason(tmp_path) -> None:
    """下载失败日志应保留请求失败原因，便于后续排查。"""

    download_log = tmp_path / "download_results.jsonl"
    recorder = ImageDownloadRecorder(str(tmp_path / "downloaded_images"), str(download_log))

    record = recorder.record_failure(
        "http://example.test/images/missing.jpg",
        FakeErrorResponse(),
        "图片请求异常 category=read_timeout type=ReadTimeout message=read timed out",
    )

    assert record["download_success"] is False
    assert record["reason"].startswith("图片请求异常 category=read_timeout")
    assert "ReadTimeout" in download_log.read_text(encoding="utf-8")
