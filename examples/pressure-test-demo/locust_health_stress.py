#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""健康检查接口——不节流压力测试脚本（用于制造限流击穿点）。"""
from __future__ import annotations
from locust import HttpUser, task

class HealthStressUser(HttpUser):
    """尽力而为打满吞吐。"""
    host = "http://127.0.0.1:8099"

    @task
    def get_health(self) -> None:
        with self.client.get("/health", name="GET /health", catch_response=True, timeout=10) as r:
            if r.status_code >= 500:
                r.failure(f"server error status={r.status_code}")
                return
            r.success()
