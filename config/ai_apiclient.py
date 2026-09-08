#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI-APIClient 调用封装（报告分析 Agent 用于 LLM 辅助分析）。

这是一个通用、可移植的 OpenAI 兼容调用封装：base_url / api_key / model 全部
从环境变量读取，不内置任何公司内部网关地址或请求头。所在环境未配置时默认指向
本地占位地址，不会真正发起调用。

可用的环境变量：
    AI_APICLIENT_BASE_URL        网关 base_url（默认 http://localhost:8000/v1）
    AI_APICLIENT_API_KEY         API Key（默认 not-configured，需自行配置）
    AI_APICLIENT_MODEL           模型名（默认 demo-model）
    AI_APICLIENT_EXTRA_HEADERS   可选：JSON 字符串，如
                                 '{"c1":"qa","c2":"test"}'，作为透传给网关的请求头。
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from typing import Any

from openai import OpenAI

try:
    from tools.logging_setup import get_logger
except ImportError:  # pragma: no cover - 允许单文件调试
    logging.basicConfig(level=logging.INFO)

    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)


logger = get_logger(__name__)


def _parse_extra_headers() -> dict[str, str]:
    """从 AI 环境的 AI_APICLIENT_EXTRA_HEADERS 环境变量解析额外请求头。

    值应为合法 JSON 字符串，解析失败或为空时返回空字典。
    """

    raw = os.getenv("AI_APICLIENT_EXTRA_HEADERS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("AI_APICLIENT_EXTRA_HEADERS 不是合法 JSON，已忽略：%s", raw)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("AI_APICLIENT_EXTRA_HEADERS 应为 JSON 对象，已忽略")
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


BASE_URL = os.getenv("AI_APICLIENT_BASE_URL", "http://localhost:8000/v1")
API_KEY = os.getenv("AI_APICLIENT_API_KEY", "not-configured")
DEFAULT_MODEL = os.getenv("AI_APICLIENT_MODEL", "demo-model")
# 可选：需要透传给网关的额外请求头，通过环境变量 JSON 注入，默认空。
DEFAULT_EXTRA_HEADERS: dict[str, str] = _parse_extra_headers()


class AiApiClient:
    """封装 AI-APIClient 接口的模型调用。"""

    def __init__(self, base_url: str = BASE_URL, api_key: str = API_KEY) -> None:
        """初始化 OpenAI-compatible 客户端。"""

        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def call(
        self,
        messages: list[dict[str, str]],
        model: str = DEFAULT_MODEL,
        stream: bool = True,
        extra_headers: dict[str, str] | None = None,
        thinking: str = "disabled",
        reasoning_effort: str = "low",
        max_retries: int = 3,
        retry_delay: int = 2,
    ) -> str:
        """调用模型并返回完整文本。"""

        start_time = time.time()
        headers = extra_headers or DEFAULT_EXTRA_HEADERS
        extra_body: dict[str, Any] = {
            "thinking": {"type": thinking},
            "reasoning_effort": reasoning_effort,
        }

        for attempt in range(1, max_retries + 1):
            try:
                logger.info("llm call started model=%s attempt=%s", model, attempt)
                response = self.client.chat.completions.create(
                    messages=messages,
                    model=model,
                    stream=stream,
                    extra_headers=headers,
                    extra_body=extra_body,
                )

                result = ""
                if stream:
                    for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            result += chunk.choices[0].delta.content
                else:
                    result = response.choices[0].message.content or ""

                elapsed = round(time.time() - start_time, 2)
                logger.info("llm call finished elapsed=%ss output_length=%s", elapsed, len(result))
                return result
            except Exception as exc:  # noqa: BLE001 - LLM 网关异常需要完整重试
                logger.warning("llm call failed attempt=%s error=%s", attempt, exc, exc_info=True)
                if attempt < max_retries:
                    time.sleep(retry_delay * (2 ** (attempt - 1)))
                    continue
                traceback.print_exc()
                return ""

        return ""
