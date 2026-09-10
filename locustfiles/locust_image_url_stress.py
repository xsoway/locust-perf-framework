# #!/usr/bin/env python
# # -*- coding: utf-8 -*-
# """图片 URL 压力测试脚本。
#
# 命名格式：locust_<业务域>_<接口或场景>_<测试类型>.py
# """
#
# from __future__ import annotations
#
#
# import hashlib
# import http.client
# import json
# import os
# import re
# import socket
# import time
# import urllib.error
# import urllib.request
# from collections import deque
# from datetime import datetime
# from pathlib import Path
# from threading import Lock
# from urllib.parse import parse_qsl, urlparse
#
# import gevent
# from locust import HttpUser, between, events, task
# from locust.exception import StopUser
# import requests
#
# from tools.config_loader import load_runtime_config
# from tools.logging_setup import get_logger
#
# runtime_config = load_runtime_config()
#
# DEFAULT_URL_FILE = Path("data/1b-url.txt")
# IMAGE_MAGIC_HEADERS = (
#     b"\xff\xd8\xff",  # jpg/jpeg
#     b"\x89PNG\r\n\x1a\n",
#     b"GIF87a",
#     b"GIF89a",
#     b"RIFF",  # webp starts with RIFF....WEBP
# )
# STREAM_CHUNK_SIZE_BYTES = int(os.getenv("LOCUST_IMAGE_STREAM_CHUNK_SIZE", "65536"))
# INCOMPLETE_READ_RETRIES = int(os.getenv("LOCUST_IMAGE_INCOMPLETE_READ_RETRIES", "2"))
# INCOMPLETE_READ_RETRY_WAIT_SECONDS = float(
#     os.getenv("LOCUST_IMAGE_INCOMPLETE_READ_RETRY_WAIT_SECONDS", "0.2")
# )
# IMAGE_REQUEST_TRANSPORT = os.getenv("LOCUST_IMAGE_TRANSPORT", "urllib").strip().lower()
# IMAGE_USER_AGENT = os.getenv("LOCUST_IMAGE_USER_AGENT", "python-urllib")
#
#
# class ImageUrlMetrics:
#     """图片 URL 压测过程中的辅助统计。"""
#
#     def __init__(self, total_urls: int, response_source_target: str = "javaapi") -> None:
#         self.lock = Lock()
#         self.total_urls = total_urls
#         self.response_source_target = response_source_target.strip().lower()
#         self.started_requests = 0
#         self.completed_requests = 0
#         self.response_source_javaapi = 0
#         self.image_success = 0
#         self.image_failure = 0
#         self.incomplete_read_retries = 0
#         self.quit_scheduled = False
#
#     def record_request_start(self) -> None:
#         """记录一次图片 URL 请求开始。"""
#
#         with self.lock:
#             self.started_requests += 1
#
#     def record_response_source(self, source_value: str | None) -> None:
#         """统计服务端响应头 x-source=javaapi 的次数。"""
#
#         if (source_value or "").strip().lower() != self.response_source_target:
#             return
#         with self.lock:
#             self.response_source_javaapi += 1
#
#     def record_image_success(self) -> None:
#         """记录一次图片加载成功。"""
#
#         with self.lock:
#             self.image_success += 1
#
#     def record_image_failure(self) -> None:
#         """记录一次图片加载失败。"""
#
#         with self.lock:
#             self.image_failure += 1
#
#     def record_incomplete_read_retry(self) -> None:
#         """记录一次响应体读取不完整后的重试。"""
#
#         with self.lock:
#             self.incomplete_read_retries += 1
#
#     def record_request_done(self) -> bool:
#         """记录一次请求完成，并返回是否已覆盖全部 URL。"""
#
#         with self.lock:
#             self.completed_requests += 1
#             if self.completed_requests < self.total_urls or self.quit_scheduled:
#                 return False
#             self.quit_scheduled = True
#             return True
#
#
# class ImageUrlQueue:
#     """线程安全的图片 URL 一次性分配队列。"""
#
#     def __init__(self, urls: list[str]) -> None:
#         self.lock = Lock()
#         self.urls = deque(urls)
#
#     def pop_next(self) -> str | None:
#         """取出下一个未访问 URL，队列为空时返回 None。"""
#
#         with self.lock:
#             if not self.urls:
#                 return None
#             return self.urls.popleft()
#
#
# class JsonlWriter:
#     """线程安全 JSONL 写入器。"""
#
#     def __init__(self, output_file: str | None) -> None:
#         self.lock = Lock()
#         self.output_file = Path(output_file) if output_file else None
#         if self.output_file:
#             self.output_file.parent.mkdir(parents=True, exist_ok=True)
#             self.output_file.write_text("", encoding="utf-8")
#
#     def write(self, detail: dict[str, object]) -> None:
#         """追加一条 JSON 记录。"""
#
#         if not self.output_file:
#             return
#         with self.lock:
#             with self.output_file.open("a", encoding="utf-8") as file:
#                 file.write(json.dumps(detail, ensure_ascii=False) + "\n")
#
#
# class SimpleRequest:
#     """urllib 请求信息适配对象。"""
#
#     def __init__(self, headers: dict[str, str]) -> None:
#         self.headers = headers
#
#
# class SimpleImageResponse:
#     """统一 requests 与 urllib 响应字段的轻量适配对象。"""
#
#     def __init__(
#         self,
#         status_code: int = 0,
#         headers: dict[str, str] | None = None,
#         content: bytes = b"",
#         error: Exception | None = None,
#         request: SimpleRequest | None = None,
#     ) -> None:
#         self.status_code = status_code
#         self.headers = headers or {}
#         self.content = content
#         self.error = error
#         self.request = request
#         self._image_content = content
#
#
# class ImageDownloadRecorder:
#     """图片下载落盘与下载结果日志记录器。"""
#
#     def __init__(self, download_dir: str | None, log_file: str | None) -> None:
#         self.download_dir = Path(download_dir) if download_dir else None
#         self.log_writer = JsonlWriter(log_file)
#         if self.download_dir:
#             self.download_dir.mkdir(parents=True, exist_ok=True)
#
#     def _filename_for_url(self, image_url: str) -> str:
#         """根据 URL 生成稳定且避免重名的本地文件名。"""
#
#         parsed = urlparse(image_url)
#         source_name = Path(parsed.path).name or "image"
#         suffix = Path(source_name).suffix or ".bin"
#         stem = Path(source_name).stem or "image"
#         url_hash = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
#         safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "image"
#         return f"{safe_stem}_{url_hash}{suffix}"
#
#     def _base_record(
#         self,
#         image_url: str,
#         response,
#         success: bool,
#         reason: str,
#         attempt: int = 1,
#         max_attempts: int = 1,
#     ) -> dict[str, object]:  # noqa: ANN001
#         """构造下载结果日志基础字段。"""
#
#         parsed = urlparse(image_url)
#         content = response_body_bytes(response)
#         return {
#             "timestamp": datetime.now().isoformat(timespec="milliseconds"),
#             "url": image_url,
#             "path": parsed.path,
#             "attempt": attempt,
#             "max_attempts": max_attempts,
#             "download_success": success,
#             "file_path": "",
#             "content_length": len(content),
#             "reason": reason,
#             "response": {
#                 "status_code": response.status_code,
#                 "content_type": header_value(response.headers, "Content-Type"),
#                 "x_source": header_value(response.headers, "x-source"),
#             },
#             "error": response_error_detail(response),
#         }
#
#     def record_success(
#         self,
#         image_url: str,
#         response,
#         attempt: int = 1,
#         max_attempts: int = 1,
#     ) -> dict[str, object]:  # noqa: ANN001
#         """保存成功加载的图片，并记录本地下载结果。"""
#
#         record = self._base_record(
#             image_url,
#             response,
#             True,
#             "download_success",
#             attempt,
#             max_attempts,
#         )
#         if not self.download_dir:
#             record["download_success"] = False
#             record["reason"] = "download_dir_not_configured"
#             self.log_writer.write(record)
#             return record
#
#         file_path = self.download_dir / self._filename_for_url(image_url)
#         try:
#             file_path.write_bytes(response_body_bytes(response))
#             record["file_path"] = str(file_path)
#         except OSError as exc:
#             record["download_success"] = False
#             record["reason"] = (
#                 "download_save_failed "
#                 f"type={exc.__class__.__name__} message={str(exc) or '-'}"
#             )
#         self.log_writer.write(record)
#         return record
#
#     def record_failure(
#         self,
#         image_url: str,
#         response,
#         reason: str,
#         attempt: int = 1,
#         max_attempts: int = 1,
#     ) -> dict[str, object]:  # noqa: ANN001
#         """记录未下载成功的图片请求及原因。"""
#
#         record = self._base_record(image_url, response, False, reason, attempt, max_attempts)
#         self.log_writer.write(record)
#         return record
#
#
# def get_script_logger():
#     """按需获取图片压测脚本 logger。"""
#
#     return get_logger("locust_image_url_stress", runtime_config.log_dir)
#
#
# def load_image_urls(url_file: Path) -> list[str]:
#     """从文本文件加载图片 URL，自动忽略空行和注释行。"""
#
#     if not url_file.exists():
#         raise FileNotFoundError(f"图片 URL 文件不存在: {url_file}")
#
#     urls = [
#         line.strip()
#         for line in url_file.read_text(encoding="utf-8").splitlines()
#         if line.strip() and not line.lstrip().startswith("#")
#     ]
#     limit = int(os.getenv("LOCUST_IMAGE_URL_LIMIT", "2000"))
#     if limit > 0:
#         urls = urls[:limit]
#
#     if not urls:
#         raise ValueError(f"图片 URL 文件为空: {url_file}")
#     return urls
#
#
# def is_image_loaded(content: bytes, content_type: str) -> bool:
#     """判断响应体是否已经完整读取为图片内容。"""
#
#     if not content:
#         return False
#
#     normalized_content_type = content_type.lower()
#     if normalized_content_type.startswith("image/"):
#         return True
#
#     # 兜底校验常见图片文件头，避免部分 CDN 未返回 content-type 时误判。
#     return any(content.startswith(prefix) for prefix in IMAGE_MAGIC_HEADERS)
#
#
# def header_value(headers: dict[str, str], name: str, default: str = "") -> str:
#     """大小写无关地读取响应头。"""
#
#     if not headers:
#         return default
#     value = headers.get(name)
#     if value is not None:
#         return str(value)
#     normalized_name = name.lower()
#     for key, item in headers.items():
#         if str(key).lower() == normalized_name:
#             return str(item)
#     return default
#
#
# def response_body_bytes(response) -> bytes:  # noqa: ANN001
#     """获取已读取的响应体字节，优先使用流式读取缓存。"""
#
#     cached_content = getattr(response, "_image_content", None)
#     if cached_content is not None:
#         return cached_content
#     try:
#         return response.content or b""
#     except requests.exceptions.RequestException as exc:
#         setattr(response, "error", exc)
#         return b""
#
#
# def expected_content_length(response) -> int:  # noqa: ANN001
#     """从响应头中读取 Content-Length，无法解析时返回 0。"""
#
#     content_length = header_value(response.headers, "Content-Length")
#     if not content_length:
#         return 0
#     try:
#         return int(content_length)
#     except ValueError:
#         return 0
#
#
# def set_response_error(response, error: Exception) -> None:  # noqa: ANN001
#     """把读取阶段异常写回响应对象，复用统一错误日志结构。"""
#
#     setattr(response, "error", error)
#
#
# def urllib_request_headers() -> dict[str, str]:
#     """构造 urllib 请求头。"""
#
#     return {
#         "User-Agent": IMAGE_USER_AGENT,
#         "Accept": "*/*",
#         "Connection": "close",
#     }
#
#
# def urllib_error_category(error: Exception) -> str:
#     """将 urllib/http.client 异常归类到统一错误分类。"""
#
#     if isinstance(error, http.client.IncompleteRead):
#         return "incomplete_read"
#     if isinstance(error, TimeoutError | socket.timeout):
#         return "read_timeout"
#     if isinstance(error, urllib.error.HTTPError):
#         return "http_error"
#     if isinstance(error, urllib.error.URLError):
#         reason = getattr(error, "reason", None)
#         if isinstance(reason, TimeoutError | socket.timeout):
#             return "read_timeout"
#         return "url_error"
#     return "request_exception"
#
#
# def read_urllib_exact(urllib_response, expected_length: int | None) -> bytes:  # noqa: ANN001
#     """从 urllib 响应中尽量读满指定长度。"""
#
#     if not expected_length or expected_length <= 0:
#         return urllib_response.read()
#
#     chunks: list[bytes] = []
#     bytes_read = 0
#     while bytes_read < expected_length:
#         chunk = urllib_response.read(expected_length - bytes_read)
#         if not chunk:
#             break
#         chunks.append(chunk)
#         bytes_read += len(chunk)
#     return b"".join(chunks)
#
#
# def fetch_image_with_urllib(image_url: str) -> SimpleImageResponse:
#     """使用 urllib.request.urlopen 下载图片响应体。"""
#
#     headers = urllib_request_headers()
#     request = urllib.request.Request(image_url, headers=headers)
#     response = SimpleImageResponse(request=SimpleRequest(headers))
#     try:
#         with urllib.request.urlopen(  # noqa: S310 - 压测脚本需要访问用户提供的 URL
#             request,
#             timeout=runtime_config.request_timeout_seconds,
#         ) as urllib_response:
#             response.status_code = urllib_response.getcode() or 0
#             response.headers = {
#                 str(key): str(value) for key, value in urllib_response.headers.items()
#             }
#             expected_length = getattr(urllib_response, "length", None)
#             if expected_length is None:
#                 expected_length = expected_content_length(response)
#             content = read_urllib_exact(urllib_response, expected_length)
#             response.content = content
#             response._image_content = content
#             if expected_length and len(content) < expected_length:
#                 remaining = expected_length - len(content)
#                 set_response_stream_error(
#                     response,
#                     "stream_incomplete_read",
#                     (
#                         f"Content-Length={expected_length}, "
#                         f"bytes_read={len(content)}, bytes_expected_remaining={remaining}"
#                     ),
#                     bytes_read=len(content),
#                     bytes_expected_remaining=remaining,
#                     bytes_expected_total=expected_length,
#                 )
#     except http.client.IncompleteRead as exc:
#         partial = exc.partial or b""
#         response.content = partial
#         response._image_content = partial
#         response.error = exc
#     except urllib.error.HTTPError as exc:
#         response.status_code = exc.code
#         response.headers = {str(key): str(value) for key, value in exc.headers.items()}
#         response.error = exc
#         try:
#             response.content = exc.read() or b""
#             response._image_content = response.content
#         except Exception:
#             response.content = b""
#             response._image_content = b""
#     except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
#         response.error = exc
#     return response
#
#
# def set_response_stream_error(
#     response,
#     category: str,
#     message: str,
#     **detail: int,
# ) -> None:  # noqa: ANN001
#     """记录未抛异常但响应体读取不完整的结构化错误。"""
#
#     setattr(
#         response,
#         "_image_stream_error",
#         {
#             "type": "StreamIncompleteRead",
#             "category": category,
#             "message": message,
#             **detail,
#         },
#     )
#
#
# def read_stream_content(response) -> tuple[bytes, str]:  # noqa: ANN001
#     """按块读取响应体，避免一次性读取导致 IncompleteRead 难以定位。"""
#
#     chunks: list[bytes] = []
#     bytes_read = 0
#     try:
#         for chunk in response.iter_content(chunk_size=STREAM_CHUNK_SIZE_BYTES):
#             if not chunk:
#                 continue
#             chunks.append(chunk)
#             bytes_read += len(chunk)
#     except requests.exceptions.RequestException as exc:
#         set_response_error(response, exc)
#         content = b"".join(chunks)
#         setattr(response, "_image_content", content)
#         error = response_error_detail(response)
#         return (
#             content,
#             "图片流式读取异常 "
#             f"category={error['category']} type={error['type']} "
#             f"message={error['message'] or '-'}",
#         )
#
#     content = b"".join(chunks)
#     setattr(response, "_image_content", content)
#     expected_total = expected_content_length(response)
#     if expected_total > 0 and bytes_read < expected_total:
#         remaining = expected_total - bytes_read
#         set_response_stream_error(
#             response,
#             "stream_incomplete_read",
#             (
#                 f"Content-Length={expected_total}, "
#                 f"bytes_read={bytes_read}, bytes_expected_remaining={remaining}"
#             ),
#             bytes_read=bytes_read,
#             bytes_expected_remaining=remaining,
#             bytes_expected_total=expected_total,
#         )
#         return (
#             content,
#             "图片流式读取未完整 "
#             f"bytes_read={bytes_read} bytes_expected_total={expected_total} "
#             f"bytes_expected_remaining={remaining}",
#         )
#     return content, ""
#
#
# def body_read_failure_reason(response) -> str:  # noqa: ANN001
#     """根据响应体读取异常生成失败原因。"""
#
#     error = response_error_detail(response)
#     if not error["type"]:
#         return ""
#     return (
#         "图片读取异常 "
#         f"category={error['category']} type={error['type']} "
#         f"message={error['message'] or '-'}"
#     )
#
#
# def locust_exception(failure_reason: str) -> Exception | None:
#     """将失败原因转换为 Locust request 事件异常对象。"""
#
#     return Exception(failure_reason) if failure_reason else None
#
#
# def should_report_attempt_to_locust(
#     failure_reason: str,
#     response,
#     attempt: int,
#     final_recorded: bool,
# ) -> bool:  # noqa: ANN001
#     """判断当前尝试是否需要进入 Locust 统计。"""
#
#     if not failure_reason:
#         return True
#     if final_recorded:
#         return True
#     return not should_retry_incomplete_read(response, failure_reason, attempt)
#
#
# def url_label(url: str) -> str:
#     """生成便于日志定位的图片 URL 标签。"""
#
#     parsed = urlparse(url)
#     return Path(parsed.path).name or parsed.path or url
#
#
# def response_text_snippet(content: bytes, limit: int = 500) -> str:
#     """提取响应体文本片段，避免报告中写入过大的二进制内容。"""
#
#     if not content:
#         return ""
#     return content[:limit].decode("utf-8", errors="replace")
#
#
# def request_headers(response) -> dict[str, str]:  # noqa: ANN001
#     """提取实际请求头，便于后续排查。"""
#
#     request = getattr(response, "request", None)
#     headers = getattr(request, "headers", {}) or {}
#     return {str(key): str(value) for key, value in dict(headers).items()}
#
#
# def response_headers(response) -> dict[str, str]:  # noqa: ANN001
#     """提取响应头，便于后续复算统计。"""
#
#     return {str(key): str(value) for key, value in dict(response.headers).items()}
#
#
# def _incomplete_read_bytes(message: str) -> dict[str, int]:
#     """从 IncompleteRead 异常文本中提取已读和剩余字节数。"""
#
#     match = re.search(r"IncompleteRead\((\d+) bytes read, (\d+) more expected\)", message)
#     if not match:
#         return {}
#     bytes_read = int(match.group(1))
#     bytes_expected_remaining = int(match.group(2))
#     return {
#         "bytes_read": bytes_read,
#         "bytes_expected_remaining": bytes_expected_remaining,
#         "bytes_expected_total": bytes_read + bytes_expected_remaining,
#     }
#
#
# def response_error_detail(response) -> dict[str, object]:  # noqa: ANN001
#     """提取 Locust/requests 捕获到的连接异常信息。"""
#
#     stream_error = getattr(response, "_image_stream_error", None)
#     if stream_error:
#         return stream_error
#
#     error = getattr(response, "error", None)
#     if not error:
#         return {"type": "", "category": "", "message": ""}
#
#     category = "request_exception"
#     message = str(error)
#     detail: dict[str, object] = {}
#     if isinstance(error, requests.exceptions.ChunkedEncodingError | http.client.IncompleteRead):
#         category = "incomplete_read"
#         detail = _incomplete_read_bytes(message)
#         if isinstance(error, http.client.IncompleteRead) and not detail:
#             partial = error.partial or b""
#             expected = error.expected or 0
#             detail = {
#                 "bytes_read": len(partial),
#                 "bytes_expected_remaining": expected,
#                 "bytes_expected_total": len(partial) + expected,
#             }
#     elif isinstance(error, requests.exceptions.ConnectTimeout):
#         category = "connect_timeout"
#     elif isinstance(error, requests.exceptions.ReadTimeout):
#         category = "read_timeout"
#     elif isinstance(error, requests.exceptions.Timeout):
#         category = "timeout"
#     elif isinstance(error, requests.exceptions.ConnectionError):
#         category = "connection_error"
#     elif isinstance(error, requests.exceptions.TooManyRedirects):
#         category = "too_many_redirects"
#     elif isinstance(error, requests.exceptions.HTTPError):
#         category = "http_error"
#     elif isinstance(error, urllib.error.URLError | TimeoutError | socket.timeout | OSError):
#         category = urllib_error_category(error)
#
#     return {
#         "type": error.__class__.__name__,
#         "category": category,
#         "message": message,
#         **detail,
#     }
#
#
# def request_failure_reason(response) -> str:  # noqa: ANN001
#     """根据响应状态和异常对象生成可定位的失败原因。"""
#
#     if response.status_code == 0:
#         error = response_error_detail(response)
#         if error["type"]:
#             return (
#                 "图片请求异常 "
#                 f"category={error['category']} type={error['type']} "
#                 f"message={error['message'] or '-'}"
#             )
#         return "图片请求异常 category=no_http_response type=Unknown message=未收到 HTTP 响应"
#     return f"图片请求失败 status={response.status_code}"
#
#
# def is_incomplete_read_failure(response, failure_reason: str) -> bool:  # noqa: ANN001
#     """判断失败是否属于响应体读取不完整，可尝试重试。"""
#
#     error = response_error_detail(response)
#     return error.get("category") in {"incomplete_read", "stream_incomplete_read"} or (
#         "IncompleteRead" in failure_reason
#     )
#
#
# def should_retry_incomplete_read(response, failure_reason: str, attempt: int) -> bool:  # noqa: ANN001
#     """判断当前读取不完整失败是否还可以重试。"""
#
#     return (
#         INCOMPLETE_READ_RETRIES > 0
#         and attempt <= INCOMPLETE_READ_RETRIES
#         and is_incomplete_read_failure(response, failure_reason)
#     )
#
#
# def build_failure_detail(
#     image_url: str,
#     response,
#     reason: str,
#     attempt: int = 1,
#     max_attempts: int = 1,
# ) -> dict[str, object]:  # noqa: ANN001
#     """构造失败请求明细。"""
#
#     parsed = urlparse(image_url)
#     content = response_body_bytes(response)
#     error = response_error_detail(response)
#     return {
#         "method": "GET",
#         "interface": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
#         "url": image_url,
#         "path": parsed.path,
#         "attempt": attempt,
#         "max_attempts": max_attempts,
#         "query": dict(parse_qsl(parsed.query, keep_blank_values=True)),
#         "request_headers": request_headers(response),
#         "failure_reason": reason,
#         "error": error,
#         "response": {
#             "status_code": response.status_code,
#             "content_type": header_value(response.headers, "Content-Type"),
#             "x_source": header_value(response.headers, "x-source"),
#             "headers": response_headers(response),
#             "content_length": len(content),
#             "body_snippet": response_text_snippet(content),
#         },
#     }
#
#
# def build_request_log(
#     image_url: str,
#     response,
#     elapsed_ms: float,
#     image_loaded: bool,
#     failure_reason: str,
#     attempt: int = 1,
#     max_attempts: int = 1,
# ) -> dict[str, object]:  # noqa: ANN001
#     """构造全量请求日志，成功和失败请求都会记录。"""
#
#     parsed = urlparse(image_url)
#     content = response_body_bytes(response)
#     error = response_error_detail(response)
#     return {
#         "timestamp": datetime.now().isoformat(timespec="milliseconds"),
#         "method": "GET",
#         "interface": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
#         "url": image_url,
#         "path": parsed.path,
#         "attempt": attempt,
#         "max_attempts": max_attempts,
#         "query": dict(parse_qsl(parsed.query, keep_blank_values=True)),
#         "request_headers": request_headers(response),
#         "response": {
#             "status_code": response.status_code,
#             "headers": response_headers(response),
#             "content_type": header_value(response.headers, "Content-Type"),
#             "x_source": header_value(response.headers, "x-source"),
#             "content_length": len(content),
#             "body_snippet": response_text_snippet(content),
#         },
#         "elapsed_ms": round(elapsed_ms, 2),
#         "image_loaded": image_loaded,
#         "failure_reason": failure_reason,
#         "error": error,
#     }
#
#
# image_url_file = Path(os.getenv("LOCUST_IMAGE_URL_FILE", str(DEFAULT_URL_FILE)))
# image_urls = load_image_urls(image_url_file)
# image_url_queue = ImageUrlQueue(image_urls)
# response_source_header = os.getenv("LOCUST_IMAGE_RESPONSE_SOURCE", "javaapi")
# metrics = ImageUrlMetrics(total_urls=len(image_urls), response_source_target=response_source_header)
# failure_detail_writer = JsonlWriter(os.getenv("LOCUST_IMAGE_FAILURE_DETAILS_FILE"))
# request_log_writer = JsonlWriter(os.getenv("LOCUST_IMAGE_REQUEST_LOG_FILE"))
# download_recorder = ImageDownloadRecorder(
#     os.getenv("LOCUST_IMAGE_DOWNLOAD_DIR"),
#     os.getenv("LOCUST_IMAGE_DOWNLOAD_LOG_FILE"),
# )
#
#
# class ImageUrlStressUser(HttpUser):
#     """图片 URL 压力测试用户：一次性覆盖 URL 列表并校验图片加载。"""
#
#     host = runtime_config.host
#     wait_time = between(
#         runtime_config.wait_time_min_seconds,
#         runtime_config.wait_time_max_seconds,
#     )
#
#     @task
#     def get_image_url(self) -> None:
#         """请求图片 URL，校验状态码和图片内容，并记录失败原因。"""
#
#         image_url = image_url_queue.pop_next()
#         if image_url is None:
#             raise StopUser()
#
#         if IMAGE_REQUEST_TRANSPORT == "urllib":
#             self.get_image_url_with_urllib(image_url)
#             return
#         self.get_image_url_with_requests(image_url)
#
#     def get_image_url_with_urllib(self, image_url: str) -> None:
#         """使用 urllib/http.client 请求图片，并手动上报 Locust 指标。"""
#
#         max_attempts = INCOMPLETE_READ_RETRIES + 1
#         final_recorded = False
#         for attempt in range(1, max_attempts + 1):
#             metrics.record_request_start()
#             started_at = time.perf_counter()
#             response = fetch_image_with_urllib(image_url)
#             image_loaded = False
#             failure_reason = ""
#             try:
#                 metrics.record_response_source(header_value(response.headers, "x-source"))
#
#                 if response.status_code != 200:
#                     failure_reason = request_failure_reason(response)
#                     metrics.record_image_failure()
#                     final_recorded = True
#                     download_recorder.record_failure(
#                         image_url,
#                         response,
#                         failure_reason,
#                         attempt,
#                         max_attempts,
#                     )
#                     failure_detail_writer.write(
#                         build_failure_detail(
#                             image_url,
#                             response,
#                             failure_reason,
#                             attempt,
#                             max_attempts,
#                         )
#                     )
#                     return
#
#                 download_failure_reason = body_read_failure_reason(response)
#                 if download_failure_reason:
#                     download_recorder.record_failure(
#                         image_url,
#                         response,
#                         download_failure_reason,
#                         attempt,
#                         max_attempts,
#                     )
#                 else:
#                     download_recorder.record_success(
#                         image_url,
#                         response,
#                         attempt,
#                         max_attempts,
#                     )
#
#                 image_loaded = True
#                 metrics.record_image_success()
#                 final_recorded = True
#                 return
#             finally:
#                 elapsed_ms = (time.perf_counter() - started_at) * 1000
#                 request_log_writer.write(
#                     build_request_log(
#                         image_url,
#                         response,
#                         elapsed_ms,
#                         image_loaded,
#                         failure_reason,
#                         attempt,
#                         max_attempts,
#                     )
#                 )
#                 if should_report_attempt_to_locust(
#                     failure_reason,
#                     response,
#                     attempt,
#                     final_recorded,
#                 ):
#                     self.environment.events.request.fire(
#                         request_type="GET",
#                         name="GET image_url",
#                         response_time=elapsed_ms,
#                         response_length=len(response_body_bytes(response)),
#                         exception=locust_exception(failure_reason),
#                         context={},
#                         url=image_url,
#                     )
#                 if final_recorded and metrics.record_request_done():
#                     gevent.spawn_later(1, self.environment.runner.quit)
#
#     def get_image_url_with_requests(self, image_url: str) -> None:
#         """使用 Locust requests 客户端请求图片。"""
#
#         max_attempts = INCOMPLETE_READ_RETRIES + 1
#         final_recorded = False
#         for attempt in range(1, max_attempts + 1):
#             metrics.record_request_start()
#             started_at = time.perf_counter()
#             with self.client.get(
#                 image_url,
#                 name="GET image_url",
#                 timeout=runtime_config.request_timeout_seconds,
#                 catch_response=True,
#                 stream=True,
#             ) as response:
#                 image_loaded = False
#                 failure_reason = ""
#                 try:
#                     metrics.record_response_source(header_value(response.headers, "x-source"))
#
#                     if response.status_code != 200:
#                         failure_reason = request_failure_reason(response)
#                         setattr(response, "_image_content", b"")
#                         metrics.record_image_failure()
#                         final_recorded = True
#                         download_recorder.record_failure(
#                             image_url,
#                             response,
#                             failure_reason,
#                             attempt,
#                             max_attempts,
#                         )
#                         failure_detail_writer.write(
#                             build_failure_detail(
#                                 image_url,
#                                 response,
#                                 failure_reason,
#                                 attempt,
#                                 max_attempts,
#                             )
#                         )
#                         response.failure(failure_reason)
#                         return
#
#                     _, stream_failure_reason = read_stream_content(response)
#                     if stream_failure_reason:
#                         download_recorder.record_failure(
#                             image_url,
#                             response,
#                             stream_failure_reason,
#                             attempt,
#                             max_attempts,
#                         )
#                     else:
#                         download_recorder.record_success(
#                             image_url,
#                             response,
#                             attempt,
#                             max_attempts,
#                         )
#
#                     image_loaded = True
#                     metrics.record_image_success()
#                     final_recorded = True
#                     response.success()
#                     return
#                 finally:
#                     request_log_writer.write(
#                         build_request_log(
#                             image_url,
#                             response,
#                             (time.perf_counter() - started_at) * 1000,
#                             image_loaded,
#                             failure_reason,
#                             attempt,
#                             max_attempts,
#                         )
#                     )
#                     if final_recorded and metrics.record_request_done():
#                         gevent.spawn_later(1, self.environment.runner.quit)
#
#
# @events.init.add_listener
# def log_image_url_start(environment, **kwargs) -> None:  # noqa: ANN001
#     """Locust 初始化时输出图片 URL 数据集信息。"""
#
#     get_script_logger().info(
#         "image url stress script loaded url_file=%s url_count=%s "
#         "response_x_source_target=%s transport=%s no_repeat=true",
#         image_url_file,
#         len(image_urls),
#         response_source_header,
#         IMAGE_REQUEST_TRANSPORT,
#     )
#
#
# @events.quitting.add_listener
# def log_image_url_summary(environment, **kwargs) -> None:  # noqa: ANN001
#     """Locust 退出时输出本脚本的辅助统计。"""
#
#     summary = {
#         "total_urls": metrics.total_urls,
#         "started_requests": metrics.started_requests,
#         "completed_requests": metrics.completed_requests,
#         "response_x_source_javaapi": metrics.response_source_javaapi,
#         "image_success": metrics.image_success,
#         "image_failure": metrics.image_failure,
#         "incomplete_read_retries": metrics.incomplete_read_retries,
#         "transport": IMAGE_REQUEST_TRANSPORT,
#         "locust_fail_ratio": environment.stats.total.fail_ratio,
#     }
#     summary_file = os.getenv("LOCUST_IMAGE_SUMMARY_FILE")
#     if summary_file:
#         Path(summary_file).parent.mkdir(parents=True, exist_ok=True)
#         Path(summary_file).write_text(
#             json.dumps(summary, ensure_ascii=False, indent=2),
#             encoding="utf-8",
#         )
#
#     get_script_logger().info(
#         "image url stress summary total_urls=%s started_requests=%s "
#         "completed_requests=%s response_x_source_javaapi=%s image_success=%s "
#         "image_failure=%s incomplete_read_retries=%s transport=%s locust_fail_ratio=%.4f",
#         summary["total_urls"],
#         summary["started_requests"],
#         summary["completed_requests"],
#         summary["response_x_source_javaapi"],
#         summary["image_success"],
#         summary["image_failure"],
#         summary["incomplete_read_retries"],
#         summary["transport"],
#         summary["locust_fail_ratio"],
#     )


















#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图片 URL 压力测试脚本。"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlparse

import gevent
from locust import HttpUser, between, events, task
from locust.exception import StopUser
import requests

from tools.config_loader import load_runtime_config
from tools.logging_setup import get_logger

runtime_config = load_runtime_config()

DEFAULT_URL_FILE = Path("data/examples/url_sample.txt")

IMAGE_MAGIC_HEADERS = (
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
)

STREAM_CHUNK_SIZE_BYTES = int(os.getenv("LOCUST_IMAGE_STREAM_CHUNK_SIZE", "65536"))
FIRST_CHUNK_READ_BYTES = int(os.getenv("LOCUST_IMAGE_FIRST_CHUNK_BYTES", "1024"))

INCOMPLETE_READ_RETRIES = int(os.getenv("LOCUST_IMAGE_INCOMPLETE_READ_RETRIES", "2"))
INCOMPLETE_READ_RETRY_WAIT_SECONDS = float(
    os.getenv("LOCUST_IMAGE_INCOMPLETE_READ_RETRY_WAIT_SECONDS", "0.2")
)

# 修改点 1：默认改成 requests，不再默认 urllib
IMAGE_REQUEST_TRANSPORT = os.getenv("LOCUST_IMAGE_TRANSPORT", "requests").strip().lower()

# 修改点 2：
# full        = 完整下载图片，适合压 CDN / 图片下载能力
# first_chunk = 只读前 1KB，适合验证图片链接是否可访问，默认推荐
# head        = 只发 HEAD，不下载 body
IMAGE_READ_MODE = os.getenv("LOCUST_IMAGE_READ_MODE", "first_chunk").strip().lower()

# 修改点 3：默认浏览器 UA，不再使用 python-urllib
IMAGE_USER_AGENT = os.getenv(
    "LOCUST_IMAGE_USER_AGENT",
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
)


def image_request_headers() -> dict[str, str]:
    """构造更接近浏览器的图片请求头。"""

    return {
        "User-Agent": IMAGE_USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }


class ImageUrlMetrics:
    """图片 URL 压测过程中的辅助统计。"""

    def __init__(self, total_urls: int, response_source_target: str = "javaapi") -> None:
        self.lock = Lock()
        self.total_urls = total_urls
        self.response_source_target = response_source_target.strip().lower()
        self.started_requests = 0
        self.completed_requests = 0
        self.response_source_javaapi = 0
        self.image_success = 0
        self.image_failure = 0
        self.incomplete_read_retries = 0
        self.quit_scheduled = False

    def record_request_start(self) -> None:
        with self.lock:
            self.started_requests += 1

    def record_response_source(self, source_value: str | None) -> None:
        if (source_value or "").strip().lower() != self.response_source_target:
            return
        with self.lock:
            self.response_source_javaapi += 1

    def record_image_success(self) -> None:
        with self.lock:
            self.image_success += 1

    def record_image_failure(self) -> None:
        with self.lock:
            self.image_failure += 1

    def record_incomplete_read_retry(self) -> None:
        with self.lock:
            self.incomplete_read_retries += 1

    def record_request_done(self) -> bool:
        with self.lock:
            self.completed_requests += 1
            if self.completed_requests < self.total_urls or self.quit_scheduled:
                return False
            self.quit_scheduled = True
            return True


class ImageUrlQueue:
    """线程安全的图片 URL 一次性分配队列。"""

    def __init__(self, urls: list[str]) -> None:
        self.lock = Lock()
        self.urls = deque(urls)

    def pop_next(self) -> str | None:
        with self.lock:
            if not self.urls:
                return None
            return self.urls.popleft()


class JsonlWriter:
    """线程安全 JSONL 写入器。"""

    def __init__(self, output_file: str | None) -> None:
        self.lock = Lock()
        self.output_file = Path(output_file) if output_file else None
        if self.output_file:
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            self.output_file.write_text("", encoding="utf-8")

    def write(self, detail: dict[str, object]) -> None:
        if not self.output_file:
            return
        with self.lock:
            with self.output_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(detail, ensure_ascii=False) + "\n")


class SimpleRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class SimpleImageResponse:
    def __init__(
        self,
        status_code: int = 0,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
        error: Exception | None = None,
        request: SimpleRequest | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.error = error
        self.request = request
        self._image_content = content


def duration_value_ms(value: str, default_unit: str = "ms") -> float | None:
    """把响应头中的耗时值转换为毫秒。"""

    if not value:
        return None

    text = str(value).strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ms|s)?", text)
    if not match:
        return None

    duration = float(match.group(1))
    unit = match.group(2) or default_unit
    if unit == "s":
        return duration * 1000
    return duration


def server_timing_ms(headers: dict[str, str]) -> float | None:
    """从 Server-Timing 响应头中提取服务端耗时。"""

    server_timing = header_value(headers, "Server-Timing")
    if not server_timing:
        return None

    match = re.search(r"(?:^|[;,]\s*)dur=(\d+(?:\.\d+)?)", server_timing)
    if not match:
        return None
    return float(match.group(1))


def response_server_rt_ms(response) -> float | None:  # noqa: ANN001
    """从响应头提取服务端耗时，拿不到时返回 None。"""

    headers = getattr(response, "headers", {}) or {}
    for header_name, default_unit in (
        ("X-Response-Time-Ms", "ms"),
        ("X-Response-Time", "ms"),
        ("X-Request-Duration-Ms", "ms"),
        ("X-Request-Duration", "ms"),
        ("X-Runtime", "s"),
        ("X-Upstream-Response-Time", "s"),
        ("X-Envoy-Upstream-Service-Time", "ms"),
    ):
        parsed = duration_value_ms(header_value(headers, header_name), default_unit)
        if parsed is not None:
            return parsed
    return server_timing_ms(headers)


def response_body_read_ms(response) -> float:  # noqa: ANN001
    """返回响应体读取耗时，单位毫秒。"""

    return float(getattr(response, "_body_read_ms", 0.0) or 0.0)


def response_request_send_ms(response) -> float:  # noqa: ANN001
    """返回发起请求到拿到响应对象的耗时，单位毫秒。"""

    return float(getattr(response, "_request_send_ms", 0.0) or 0.0)


def response_client_rt_ms(response, fallback_ms: float = 0.0) -> float:  # noqa: ANN001
    """返回客户端完整请求耗时，单位毫秒。"""

    return float(getattr(response, "_client_rt_ms", fallback_ms) or 0.0)


def response_post_process_ms(response) -> float:  # noqa: ANN001
    """返回断言、统计和日志准备等后处理耗时，单位毫秒。"""

    return float(getattr(response, "_post_process_ms", 0.0) or 0.0)


def mark_request_send_ms(response, elapsed_ms: float | None = None) -> float:  # noqa: ANN001
    """记录请求发起到拿到响应对象的耗时。"""

    if elapsed_ms is None:
        request_meta = getattr(response, "request_meta", None)
        if isinstance(request_meta, dict):
            elapsed_ms = float(request_meta.get("response_time", 0.0) or 0.0)
        else:
            elapsed_ms = 0.0
    setattr(response, "_request_send_ms", elapsed_ms)
    return elapsed_ms


def mark_body_read_ms(response, started_at: float) -> float:  # noqa: ANN001
    """记录响应体读取耗时。"""

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    setattr(response, "_body_read_ms", elapsed_ms)
    return elapsed_ms


def mark_client_rt_ms(response, started_at: float) -> float:  # noqa: ANN001
    """记录 Locust 口径的客户端请求耗时。"""

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    setattr(response, "_client_rt_ms", elapsed_ms)
    return elapsed_ms


def mark_post_process_ms(response, client_rt_ms: float | None = None) -> float:  # noqa: ANN001
    """记录客户端总耗时中除请求发送和 body 读取外的耗时。"""

    if client_rt_ms is None:
        client_rt_ms = response_client_rt_ms(response)
    post_process_ms = max(
        client_rt_ms - response_request_send_ms(response) - response_body_read_ms(response),
        0.0,
    )
    setattr(response, "_post_process_ms", post_process_ms)
    return post_process_ms


def mark_client_timing_breakdown(response, started_at: float) -> float:  # noqa: ANN001
    """同步客户端总耗时、请求发送耗时和后处理耗时。"""

    client_rt_ms = mark_client_rt_ms(response, started_at)
    if response_request_send_ms(response) <= 0:
        mark_request_send_ms(response, max(client_rt_ms - response_body_read_ms(response), 0.0))
    mark_post_process_ms(response, client_rt_ms)
    return client_rt_ms


def sync_locust_request_meta(response, elapsed_ms: float) -> None:  # noqa: ANN001
    """把 Locust 统计口径同步为完整客户端耗时。"""

    request_meta = getattr(response, "request_meta", None)
    if not isinstance(request_meta, dict):
        return
    request_meta["response_time"] = elapsed_ms
    request_meta["response_length"] = len(response_body_bytes(response))


def format_optional_ms(value: float | None) -> float | str:
    """格式化可缺省的毫秒指标。"""

    if value is None:
        return ""
    return round(value, 2)


class ImageDownloadRecorder:
    """图片下载落盘与下载结果日志记录器。"""

    def __init__(self, download_dir: str | None, log_file: str | None) -> None:
        self.download_dir = Path(download_dir) if download_dir else None
        self.log_writer = JsonlWriter(log_file)
        if self.download_dir:
            self.download_dir.mkdir(parents=True, exist_ok=True)

    def _filename_for_url(self, image_url: str) -> str:
        parsed = urlparse(image_url)
        source_name = Path(parsed.path).name or "image"
        suffix = Path(source_name).suffix or ".bin"
        stem = Path(source_name).stem or "image"
        url_hash = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "image"
        return f"{safe_stem}_{url_hash}{suffix}"

    def _base_record(
        self,
        image_url: str,
        response,
        success: bool,
        reason: str,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> dict[str, object]:
        parsed = urlparse(image_url)
        content = response_body_bytes(response)
        server_rt = response_server_rt_ms(response)
        return {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "url": image_url,
            "path": parsed.path,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "download_success": success,
            "file_path": "",
            "client_rt_ms": round(response_client_rt_ms(response), 2),
            "request_send_ms": round(response_request_send_ms(response), 2),
            "server_rt_ms": format_optional_ms(server_rt),
            "body_read_ms": round(response_body_read_ms(response), 2),
            "post_process_ms": round(response_post_process_ms(response), 2),
            "content_length": len(content),
            "reason": reason,
            "read_mode": IMAGE_READ_MODE,
            "response": {
                "status_code": response.status_code,
                "content_type": header_value(response.headers, "Content-Type"),
                "content_length_header": header_value(response.headers, "Content-Length"),
                "x_source": header_value(response.headers, "x-source"),
            },
            "error": response_error_detail(response),
        }

    def record_success(
        self,
        image_url: str,
        response,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> dict[str, object]:
        record = self._base_record(
            image_url,
            response,
            True,
            "download_success",
            attempt,
            max_attempts,
        )

        # first_chunk/head 模式下不建议落盘，因为不是完整图片
        if IMAGE_READ_MODE != "full":
            record["download_success"] = True
            record["reason"] = f"{IMAGE_READ_MODE}_success_no_file_saved"
            self.log_writer.write(record)
            return record

        if not self.download_dir:
            record["download_success"] = False
            record["reason"] = "download_dir_not_configured"
            self.log_writer.write(record)
            return record

        file_path = self.download_dir / self._filename_for_url(image_url)
        try:
            file_path.write_bytes(response_body_bytes(response))
            record["file_path"] = str(file_path)
        except OSError as exc:
            record["download_success"] = False
            record["reason"] = (
                "download_save_failed "
                f"type={exc.__class__.__name__} message={str(exc) or '-'}"
            )
        self.log_writer.write(record)
        return record

    def record_failure(
        self,
        image_url: str,
        response,
        reason: str,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> dict[str, object]:
        record = self._base_record(image_url, response, False, reason, attempt, max_attempts)
        self.log_writer.write(record)
        return record


def get_script_logger():
    return get_logger("locust_image_url_stress", runtime_config.log_dir)


def load_image_urls(url_file: Path) -> list[str]:
    if not url_file.exists():
        raise FileNotFoundError(f"图片 URL 文件不存在: {url_file}")

    urls = [
        line.strip()
        for line in url_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    limit = int(os.getenv("LOCUST_IMAGE_URL_LIMIT", "2000"))
    if limit > 0:
        urls = urls[:limit]

    if not urls:
        raise ValueError(f"图片 URL 文件为空: {url_file}")
    return urls


def is_image_loaded(content: bytes, content_type: str) -> bool:
    """判断响应是否为图片。"""

    normalized_content_type = content_type.lower()

    if normalized_content_type.startswith("image/"):
        return True

    if not content:
        return False

    return any(content.startswith(prefix) for prefix in IMAGE_MAGIC_HEADERS)


def assert_image_response(response, content: bytes, full_download: bool = False) -> str:
    """断言图片响应是否符合预期，返回空字符串表示通过。"""

    if response.status_code != 200:
        return f"status错误 status={response.status_code}"

    content_type = header_value(response.headers, "Content-Type")
    if not content_type.lower().startswith("image/"):
        return f"Content-Type错误 content_type={content_type or '-'}"

    content_length = header_value(response.headers, "Content-Length")
    if content_length:
        try:
            expected = int(content_length)
            if expected <= 0:
                return f"Content-Length异常 content_length={content_length}"
        except ValueError:
            return f"Content-Length不是数字 content_length={content_length}"

    if not content:
        return "响应体为空"

    if full_download and content_length:
        expected = int(content_length)
        actual = len(content)
        if actual != expected:
            return f"图片未完整下载 expected={expected} actual={actual}"

    return ""


def image_response_failure_reason(
    response,
    content: bytes,
    full_download: bool = False,
) -> str:
    """返回图片响应失败原因，连接异常优先展示底层错误。"""

    if response.status_code == 0:
        return request_failure_reason(response)
    return assert_image_response(response, content, full_download=full_download)


def header_value(headers: dict[str, str], name: str, default: str = "") -> str:
    if not headers:
        return default

    value = headers.get(name)
    if value is not None:
        return str(value)

    normalized_name = name.lower()
    for key, item in headers.items():
        if str(key).lower() == normalized_name:
            return str(item)

    return default


def response_body_bytes(response) -> bytes:
    cached_content = getattr(response, "_image_content", None)
    if cached_content is not None:
        return cached_content

    try:
        return response.content or b""
    except requests.exceptions.RequestException as exc:
        setattr(response, "error", exc)
        return b""


def expected_content_length(response) -> int:
    content_length = header_value(response.headers, "Content-Length")
    if not content_length:
        return 0
    try:
        return int(content_length)
    except ValueError:
        return 0


def set_response_error(response, error: Exception) -> None:
    setattr(response, "error", error)


def urllib_request_headers() -> dict[str, str]:
    return image_request_headers()


def urllib_error_category(error: Exception) -> str:
    if isinstance(error, http.client.IncompleteRead):
        return "incomplete_read"
    if isinstance(error, TimeoutError | socket.timeout):
        return "read_timeout"
    if isinstance(error, urllib.error.HTTPError):
        return "http_error"
    if isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        if isinstance(reason, TimeoutError | socket.timeout):
            return "read_timeout"
        return "url_error"
    return "request_exception"


def read_urllib_exact(urllib_response, expected_length: int | None) -> bytes:
    if not expected_length or expected_length <= 0:
        return urllib_response.read()

    chunks: list[bytes] = []
    bytes_read = 0

    while bytes_read < expected_length:
        chunk = urllib_response.read(expected_length - bytes_read)
        if not chunk:
            break
        chunks.append(chunk)
        bytes_read += len(chunk)

    return b"".join(chunks)


def read_urllib_first_chunk(urllib_response, max_bytes: int = FIRST_CHUNK_READ_BYTES) -> bytes:
    chunk = urllib_response.read(max_bytes)
    return chunk or b""


def fetch_image_with_urllib(image_url: str) -> SimpleImageResponse:
    headers = urllib_request_headers()
    request = urllib.request.Request(image_url, headers=headers)
    response = SimpleImageResponse(request=SimpleRequest(headers))
    body_started_at: float | None = None

    try:
        with urllib.request.urlopen(
            request,
            timeout=runtime_config.request_timeout_seconds,
        ) as urllib_response:
            response.status_code = urllib_response.getcode() or 0
            response.headers = {
                str(key): str(value) for key, value in urllib_response.headers.items()
            }

            if IMAGE_READ_MODE == "head":
                response.content = b""
                response._image_content = b""
                setattr(response, "_body_read_ms", 0.0)
                return response

            if IMAGE_READ_MODE == "first_chunk":
                body_started_at = time.perf_counter()
                content = read_urllib_first_chunk(urllib_response)
                mark_body_read_ms(response, body_started_at)
                response.content = content
                response._image_content = content
                return response

            expected_length = getattr(urllib_response, "length", None)
            if expected_length is None:
                expected_length = expected_content_length(response)

            body_started_at = time.perf_counter()
            content = read_urllib_exact(urllib_response, expected_length)
            mark_body_read_ms(response, body_started_at)
            response.content = content
            response._image_content = content

            if expected_length and len(content) < expected_length:
                remaining = expected_length - len(content)
                set_response_stream_error(
                    response,
                    "stream_incomplete_read",
                    (
                        f"Content-Length={expected_length}, "
                        f"bytes_read={len(content)}, bytes_expected_remaining={remaining}"
                    ),
                    bytes_read=len(content),
                    bytes_expected_remaining=remaining,
                    bytes_expected_total=expected_length,
                )

    except http.client.IncompleteRead as exc:
        if not hasattr(response, "_body_read_ms"):
            if body_started_at is None:
                setattr(response, "_body_read_ms", 0.0)
            else:
                mark_body_read_ms(response, body_started_at)
        partial = exc.partial or b""
        response.content = partial
        response._image_content = partial
        response.error = exc
    except urllib.error.HTTPError as exc:
        response.status_code = exc.code
        response.headers = {str(key): str(value) for key, value in exc.headers.items()}
        response.error = exc
        try:
            body_started_at = time.perf_counter()
            response.content = exc.read() or b""
            mark_body_read_ms(response, body_started_at)
            response._image_content = response.content
        except Exception:
            response.content = b""
            response._image_content = b""
            setattr(response, "_body_read_ms", 0.0)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        setattr(response, "_body_read_ms", 0.0)
        response.error = exc

    return response


def set_response_stream_error(
    response,
    category: str,
    message: str,
    **detail: int,
) -> None:
    setattr(
        response,
        "_image_stream_error",
        {
            "type": "StreamIncompleteRead",
            "category": category,
            "message": message,
            **detail,
        },
    )


def read_stream_content(response) -> tuple[bytes, str]:
    """完整读取响应体。适合压测图片完整下载。"""

    chunks: list[bytes] = []
    bytes_read = 0

    try:
        for chunk in response.iter_content(chunk_size=STREAM_CHUNK_SIZE_BYTES):
            if not chunk:
                continue
            chunks.append(chunk)
            bytes_read += len(chunk)
    except requests.exceptions.RequestException as exc:
        set_response_error(response, exc)
        content = b"".join(chunks)
        setattr(response, "_image_content", content)
        error = response_error_detail(response)
        return (
            content,
            "图片流式读取异常 "
            f"category={error['category']} type={error['type']} "
            f"message={error['message'] or '-'}",
        )

    content = b"".join(chunks)
    setattr(response, "_image_content", content)

    expected_total = expected_content_length(response)
    if expected_total > 0 and bytes_read < expected_total:
        remaining = expected_total - bytes_read
        set_response_stream_error(
            response,
            "stream_incomplete_read",
            (
                f"Content-Length={expected_total}, "
                f"bytes_read={bytes_read}, bytes_expected_remaining={remaining}"
            ),
            bytes_read=bytes_read,
            bytes_expected_remaining=remaining,
            bytes_expected_total=expected_total,
        )
        return (
            content,
            "图片流式读取未完整 "
            f"bytes_read={bytes_read} bytes_expected_total={expected_total} "
            f"bytes_expected_remaining={remaining}",
        )

    return content, ""


def read_first_chunk_content(response, max_bytes: int = FIRST_CHUNK_READ_BYTES) -> tuple[bytes, str]:
    """只读前 N 字节。适合验证图片 URL 可访问，不强制下载完整图片。"""

    chunks: list[bytes] = []
    bytes_read = 0

    try:
        for chunk in response.iter_content(chunk_size=min(1024, max_bytes)):
            if not chunk:
                continue
            chunks.append(chunk)
            bytes_read += len(chunk)
            if bytes_read >= max_bytes:
                break
    except requests.exceptions.RequestException as exc:
        set_response_error(response, exc)
        content = b"".join(chunks)
        setattr(response, "_image_content", content)
        error = response_error_detail(response)
        return (
            content,
            "图片首包读取异常 "
            f"category={error['category']} type={error['type']} "
            f"message={error['message'] or '-'}",
        )

    content = b"".join(chunks)
    setattr(response, "_image_content", content)
    return content, ""


def body_read_failure_reason(response) -> str:
    error = response_error_detail(response)
    if not error["type"]:
        return ""
    return (
        "图片读取异常 "
        f"category={error['category']} type={error['type']} "
        f"message={error['message'] or '-'}"
    )


def locust_exception(failure_reason: str) -> Exception | None:
    return Exception(failure_reason) if failure_reason else None


def should_report_attempt_to_locust(
    failure_reason: str,
    response,
    attempt: int,
    final_recorded: bool,
) -> bool:
    if not failure_reason:
        return True
    if final_recorded:
        return True
    return not should_retry_incomplete_read(response, failure_reason, attempt)


def url_label(url: str) -> str:
    parsed = urlparse(url)
    return Path(parsed.path).name or parsed.path or url


def response_text_snippet(content: bytes, limit: int = 500) -> str:
    if not content:
        return ""
    return content[:limit].decode("utf-8", errors="replace")


def request_headers(response) -> dict[str, str]:
    request = getattr(response, "request", None)
    headers = getattr(request, "headers", {}) or {}
    return {str(key): str(value) for key, value in dict(headers).items()}


def response_headers(response) -> dict[str, str]:
    return {str(key): str(value) for key, value in dict(response.headers).items()}


def _incomplete_read_bytes(message: str) -> dict[str, int]:
    match = re.search(r"IncompleteRead\((\d+) bytes read, (\d+) more expected\)", message)
    if not match:
        return {}

    bytes_read = int(match.group(1))
    bytes_expected_remaining = int(match.group(2))

    return {
        "bytes_read": bytes_read,
        "bytes_expected_remaining": bytes_expected_remaining,
        "bytes_expected_total": bytes_read + bytes_expected_remaining,
    }


def response_error_detail(response) -> dict[str, object]:
    stream_error = getattr(response, "_image_stream_error", None)
    if stream_error:
        return stream_error

    error = getattr(response, "error", None)
    if not error:
        return {"type": "", "category": "", "message": ""}

    category = "request_exception"
    message = str(error)
    detail: dict[str, object] = {}

    if isinstance(error, requests.exceptions.ChunkedEncodingError | http.client.IncompleteRead):
        category = "incomplete_read"
        detail = _incomplete_read_bytes(message)
        if isinstance(error, http.client.IncompleteRead) and not detail:
            partial = error.partial or b""
            expected = error.expected or 0
            detail = {
                "bytes_read": len(partial),
                "bytes_expected_remaining": expected,
                "bytes_expected_total": len(partial) + expected,
            }
    elif isinstance(error, requests.exceptions.ConnectTimeout):
        category = "connect_timeout"
    elif isinstance(error, requests.exceptions.ReadTimeout):
        category = "read_timeout"
    elif isinstance(error, requests.exceptions.Timeout):
        category = "timeout"
    elif isinstance(error, requests.exceptions.ConnectionError):
        category = "connection_error"
    elif isinstance(error, requests.exceptions.TooManyRedirects):
        category = "too_many_redirects"
    elif isinstance(error, requests.exceptions.HTTPError):
        category = "http_error"
    elif isinstance(error, urllib.error.URLError | TimeoutError | socket.timeout | OSError):
        category = urllib_error_category(error)

    return {
        "type": error.__class__.__name__,
        "category": category,
        "message": message,
        **detail,
    }


def request_failure_reason(response) -> str:
    if response.status_code == 0:
        error = response_error_detail(response)
        if error["type"]:
            return (
                "图片请求异常 "
                f"category={error['category']} type={error['type']} "
                f"message={error['message'] or '-'}"
            )
        return "图片请求异常 category=no_http_response type=Unknown message=未收到 HTTP 响应"

    return f"图片请求失败 status={response.status_code}"


def is_incomplete_read_failure(response, failure_reason: str) -> bool:
    error = response_error_detail(response)
    return error.get("category") in {"incomplete_read", "stream_incomplete_read"} or (
        "IncompleteRead" in failure_reason
    )


def should_retry_incomplete_read(response, failure_reason: str, attempt: int) -> bool:
    return (
        IMAGE_READ_MODE == "full"
        and INCOMPLETE_READ_RETRIES > 0
        and attempt <= INCOMPLETE_READ_RETRIES
        and is_incomplete_read_failure(response, failure_reason)
    )


def build_failure_detail(
    image_url: str,
    response,
    reason: str,
    attempt: int = 1,
    max_attempts: int = 1,
) -> dict[str, object]:
    parsed = urlparse(image_url)
    content = response_body_bytes(response)
    error = response_error_detail(response)
    server_rt = response_server_rt_ms(response)

    return {
        "method": "GET",
        "interface": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        "url": image_url,
        "path": parsed.path,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "read_mode": IMAGE_READ_MODE,
        "client_rt_ms": round(response_client_rt_ms(response), 2),
        "request_send_ms": round(response_request_send_ms(response), 2),
        "server_rt_ms": format_optional_ms(server_rt),
        "body_read_ms": round(response_body_read_ms(response), 2),
        "post_process_ms": round(response_post_process_ms(response), 2),
        "content_length": len(content),
        "query": dict(parse_qsl(parsed.query, keep_blank_values=True)),
        "request_headers": request_headers(response),
        "failure_reason": reason,
        "error": error,
        "response": {
            "status_code": response.status_code,
            "content_type": header_value(response.headers, "Content-Type"),
            "content_length_header": header_value(response.headers, "Content-Length"),
            "x_source": header_value(response.headers, "x-source"),
            "headers": response_headers(response),
            "content_length": len(content),
            "body_snippet": response_text_snippet(content),
        },
    }


def build_request_log(
    image_url: str,
    response,
    elapsed_ms: float,
    image_loaded: bool,
    failure_reason: str,
    attempt: int = 1,
    max_attempts: int = 1,
) -> dict[str, object]:
    parsed = urlparse(image_url)
    content = response_body_bytes(response)
    error = response_error_detail(response)
    client_rt = response_client_rt_ms(response, elapsed_ms)
    server_rt = response_server_rt_ms(response)

    return {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "method": "GET",
        "interface": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        "url": image_url,
        "path": parsed.path,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "read_mode": IMAGE_READ_MODE,
        "client_rt_ms": round(client_rt, 2),
        "request_send_ms": round(response_request_send_ms(response), 2),
        "server_rt_ms": format_optional_ms(server_rt),
        "body_read_ms": round(response_body_read_ms(response), 2),
        "post_process_ms": round(response_post_process_ms(response), 2),
        "content_length": len(content),
        "query": dict(parse_qsl(parsed.query, keep_blank_values=True)),
        "request_headers": request_headers(response),
        "response": {
            "status_code": response.status_code,
            "headers": response_headers(response),
            "content_type": header_value(response.headers, "Content-Type"),
            "content_length_header": header_value(response.headers, "Content-Length"),
            "x_source": header_value(response.headers, "x-source"),
            "content_length": len(content),
            "body_snippet": response_text_snippet(content),
        },
        "elapsed_ms": round(elapsed_ms, 2),
        "image_loaded": image_loaded,
        "failure_reason": failure_reason,
        "error": error,
    }


image_url_file = Path(os.getenv("LOCUST_IMAGE_URL_FILE", str(DEFAULT_URL_FILE)))
image_urls = load_image_urls(image_url_file)
image_url_queue = ImageUrlQueue(image_urls)

response_source_header = os.getenv("LOCUST_IMAGE_RESPONSE_SOURCE", "javaapi")

metrics = ImageUrlMetrics(
    total_urls=len(image_urls),
    response_source_target=response_source_header,
)

failure_detail_writer = JsonlWriter(os.getenv("LOCUST_IMAGE_FAILURE_DETAILS_FILE"))
request_log_writer = JsonlWriter(os.getenv("LOCUST_IMAGE_REQUEST_LOG_FILE"))

download_recorder = ImageDownloadRecorder(
    os.getenv("LOCUST_IMAGE_DOWNLOAD_DIR"),
    os.getenv("LOCUST_IMAGE_DOWNLOAD_LOG_FILE"),
)


class ImageUrlStressUser(HttpUser):
    """图片 URL 压力测试用户：一次性覆盖 URL 列表并校验图片加载。"""

    host = runtime_config.host

    wait_time = between(
        runtime_config.wait_time_min_seconds,
        runtime_config.wait_time_max_seconds,
    )

    @task
    def get_image_url(self) -> None:
        image_url = image_url_queue.pop_next()
        if image_url is None:
            raise StopUser()

        if IMAGE_REQUEST_TRANSPORT == "urllib":
            self.get_image_url_with_urllib(image_url)
            return

        self.get_image_url_with_requests(image_url)

    def get_image_url_with_urllib(self, image_url: str) -> None:
        max_attempts = INCOMPLETE_READ_RETRIES + 1 if IMAGE_READ_MODE == "full" else 1
        final_recorded = False

        for attempt in range(1, max_attempts + 1):
            metrics.record_request_start()
            started_at = time.perf_counter()
            response = fetch_image_with_urllib(image_url)
            image_loaded = False
            failure_reason = ""

            try:
                metrics.record_response_source(header_value(response.headers, "x-source"))

                content = response_body_bytes(response)
                failure_reason = image_response_failure_reason(
                    response,
                    content,
                    full_download=IMAGE_READ_MODE == "full",
                )
                if failure_reason:
                    metrics.record_image_failure()
                    final_recorded = True
                    mark_client_timing_breakdown(response, started_at)

                    download_recorder.record_failure(
                        image_url,
                        response,
                        failure_reason,
                        attempt,
                        max_attempts,
                    )

                    failure_detail_writer.write(
                        build_failure_detail(
                            image_url,
                            response,
                            failure_reason,
                            attempt,
                            max_attempts,
                        )
                    )
                    return

                download_failure_reason = body_read_failure_reason(response)

                if download_failure_reason:
                    mark_client_timing_breakdown(response, started_at)
                    download_recorder.record_failure(
                        image_url,
                        response,
                        download_failure_reason,
                        attempt,
                        max_attempts,
                    )
                else:
                    mark_client_timing_breakdown(response, started_at)
                    download_recorder.record_success(
                        image_url,
                        response,
                        attempt,
                        max_attempts,
                    )

                image_loaded = True
                metrics.record_image_success()
                final_recorded = True
                return

            finally:
                elapsed_ms = mark_client_timing_breakdown(response, started_at)

                request_log_writer.write(
                    build_request_log(
                        image_url,
                        response,
                        elapsed_ms,
                        image_loaded,
                        failure_reason,
                        attempt,
                        max_attempts,
                    )
                )

                if should_report_attempt_to_locust(
                    failure_reason,
                    response,
                    attempt,
                    final_recorded,
                ):
                    self.environment.events.request.fire(
                        request_type="GET",
                        name=f"GET image_url {IMAGE_READ_MODE}",
                        response_time=elapsed_ms,
                        response_length=len(response_body_bytes(response)),
                        exception=locust_exception(failure_reason),
                        context={},
                        url=image_url,
                    )

                if final_recorded and metrics.record_request_done():
                    gevent.spawn_later(1, self.environment.runner.quit)

    def get_image_url_with_requests(self, image_url: str) -> None:
        max_attempts = INCOMPLETE_READ_RETRIES + 1 if IMAGE_READ_MODE == "full" else 1
        final_recorded = False

        for attempt in range(1, max_attempts + 1):
            metrics.record_request_start()
            started_at = time.perf_counter()

            if IMAGE_READ_MODE == "head":
                with self.client.head(
                    image_url,
                    name="HEAD image_url",
                    timeout=runtime_config.request_timeout_seconds,
                    catch_response=True,
                    headers=image_request_headers(),
                ) as response:
                    image_loaded = False
                    failure_reason = ""
                    mark_request_send_ms(response)

                    try:
                        setattr(response, "_image_content", b"")
                        setattr(response, "_body_read_ms", 0.0)

                        metrics.record_response_source(
                            header_value(response.headers, "x-source")
                        )

                        failure_reason = image_response_failure_reason(
                            response,
                            response_body_bytes(response),
                            full_download=False,
                        )
                        if failure_reason:
                            metrics.record_image_failure()
                            final_recorded = True
                            mark_client_timing_breakdown(response, started_at)

                            download_recorder.record_failure(
                                image_url,
                                response,
                                failure_reason,
                                attempt,
                                max_attempts,
                            )

                            failure_detail_writer.write(
                                build_failure_detail(
                                    image_url,
                                    response,
                                    failure_reason,
                                    attempt,
                                    max_attempts,
                                )
                            )

                            response.failure(failure_reason)
                            return

                        image_loaded = True
                        metrics.record_image_success()
                        final_recorded = True
                        mark_client_timing_breakdown(response, started_at)

                        download_recorder.record_success(
                            image_url,
                            response,
                            attempt,
                            max_attempts,
                        )

                        response.success()
                        return

                    finally:
                        elapsed_ms = mark_client_timing_breakdown(response, started_at)
                        sync_locust_request_meta(response, elapsed_ms)
                        request_log_writer.write(
                            build_request_log(
                                image_url,
                                response,
                                elapsed_ms,
                                image_loaded,
                                failure_reason,
                                attempt,
                                max_attempts,
                            )
                        )

                        if final_recorded and metrics.record_request_done():
                            gevent.spawn_later(1, self.environment.runner.quit)

            with self.client.get(
                image_url,
                name=f"GET image_url {IMAGE_READ_MODE}",
                timeout=runtime_config.request_timeout_seconds,
                catch_response=True,
                stream=True,
                headers=image_request_headers(),
            ) as response:
                image_loaded = False
                failure_reason = ""
                mark_request_send_ms(response)

                try:
                    metrics.record_response_source(header_value(response.headers, "x-source"))

                    if response.status_code != 200:
                        failure_reason = image_response_failure_reason(
                            response,
                            b"",
                            full_download=IMAGE_READ_MODE == "full",
                        )
                        setattr(response, "_image_content", b"")
                        setattr(response, "_body_read_ms", 0.0)
                        metrics.record_image_failure()
                        final_recorded = True
                        mark_client_timing_breakdown(response, started_at)

                        download_recorder.record_failure(
                            image_url,
                            response,
                            failure_reason,
                            attempt,
                            max_attempts,
                        )

                        failure_detail_writer.write(
                            build_failure_detail(
                                image_url,
                                response,
                                failure_reason,
                                attempt,
                                max_attempts,
                            )
                        )

                        response.failure(failure_reason)
                        return

                    if IMAGE_READ_MODE == "full":
                        body_started_at = time.perf_counter()
                        content, stream_failure_reason = read_stream_content(response)
                    else:
                        body_started_at = time.perf_counter()
                        content, stream_failure_reason = read_first_chunk_content(response)
                    mark_body_read_ms(response, body_started_at)

                    failure_reason = image_response_failure_reason(
                        response,
                        content,
                        full_download=IMAGE_READ_MODE == "full",
                    )
                    if failure_reason:
                        metrics.record_image_failure()
                        final_recorded = True
                        mark_client_timing_breakdown(response, started_at)
                        download_recorder.record_failure(
                            image_url,
                            response,
                            stream_failure_reason or failure_reason,
                            attempt,
                            max_attempts,
                        )

                        failure_detail_writer.write(
                            build_failure_detail(
                                image_url,
                                response,
                                failure_reason,
                                attempt,
                                max_attempts,
                            )
                        )

                        response.failure(failure_reason)
                        return

                    if stream_failure_reason:
                        mark_client_timing_breakdown(response, started_at)
                        download_recorder.record_failure(
                            image_url,
                            response,
                            stream_failure_reason,
                            attempt,
                            max_attempts,
                        )
                    else:
                        mark_client_timing_breakdown(response, started_at)
                        download_recorder.record_success(
                            image_url,
                            response,
                            attempt,
                            max_attempts,
                        )

                    image_loaded = True
                    metrics.record_image_success()
                    final_recorded = True
                    response.success()
                    return

                finally:
                    elapsed_ms = mark_client_timing_breakdown(response, started_at)
                    sync_locust_request_meta(response, elapsed_ms)
                    request_log_writer.write(
                        build_request_log(
                            image_url,
                            response,
                            elapsed_ms,
                            image_loaded,
                            failure_reason,
                            attempt,
                            max_attempts,
                        )
                    )

                    if final_recorded and metrics.record_request_done():
                        gevent.spawn_later(1, self.environment.runner.quit)


@events.init.add_listener
def log_image_url_start(environment, **kwargs) -> None:
    get_script_logger().info(
        "image url stress script loaded url_file=%s url_count=%s "
        "response_x_source_target=%s transport=%s read_mode=%s no_repeat=true",
        image_url_file,
        len(image_urls),
        response_source_header,
        IMAGE_REQUEST_TRANSPORT,
        IMAGE_READ_MODE,
    )


@events.quitting.add_listener
def log_image_url_summary(environment, **kwargs) -> None:
    summary = {
        "total_urls": metrics.total_urls,
        "started_requests": metrics.started_requests,
        "completed_requests": metrics.completed_requests,
        "response_x_source_javaapi": metrics.response_source_javaapi,
        "image_success": metrics.image_success,
        "image_failure": metrics.image_failure,
        "incomplete_read_retries": metrics.incomplete_read_retries,
        "transport": IMAGE_REQUEST_TRANSPORT,
        "read_mode": IMAGE_READ_MODE,
        "locust_fail_ratio": environment.stats.total.fail_ratio,
    }

    summary_file = os.getenv("LOCUST_IMAGE_SUMMARY_FILE")

    if summary_file:
        Path(summary_file).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_file).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    get_script_logger().info(
        "image url stress summary total_urls=%s started_requests=%s "
        "completed_requests=%s response_x_source_javaapi=%s image_success=%s "
        "image_failure=%s incomplete_read_retries=%s transport=%s read_mode=%s "
        "locust_fail_ratio=%.4f",
        summary["total_urls"],
        summary["started_requests"],
        summary["completed_requests"],
        summary["response_x_source_javaapi"],
        summary["image_success"],
        summary["image_failure"],
        summary["incomplete_read_retries"],
        summary["transport"],
        summary["read_mode"],
        summary["locust_fail_ratio"],
    )
