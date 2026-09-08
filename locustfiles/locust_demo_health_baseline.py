#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""示例 Locust 基准测试脚本。

命名格式：locust_<业务域>_<接口或场景>_<测试类型>.py
"""

from __future__ import annotations

from locust import HttpUser, between, task

from tools.config_loader import load_runtime_config

runtime_config = load_runtime_config()


class DemoHealthUser(HttpUser):
    """示例用户：访问健康检查接口。"""

    host = runtime_config.host
    wait_time = between(
        runtime_config.wait_time_min_seconds,
        runtime_config.wait_time_max_seconds,
    )

    @task
    def get_health(self) -> None:
        """请求健康检查接口并记录业务成功/失败。"""

        with self.client.get(
            "/health",
            name="GET /health",
            timeout=runtime_config.request_timeout_seconds,
            catch_response=True,
        ) as response:
            if response.status_code >= 500:
                response.failure(f"server error status={response.status_code}")
                return
            response.success()
