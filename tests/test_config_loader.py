#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置读取测试。"""

from __future__ import annotations

from pathlib import Path

from tools.config_loader import load_runtime_config


def test_load_runtime_config(tmp_path: Path) -> None:
    """可以读取运行配置。"""

    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[runtime]\n"
        "host = http://example.test\n"
        "environment = test\n"
        "request_timeout_seconds = 0\n"
        "wait_time_min_seconds = 0\n"
        "wait_time_max_seconds = 0\n"
        "\n"
        "[logging]\n"
        "level = INFO\n"
        "log_dir = logs\n",
        encoding="utf-8",
    )

    config = load_runtime_config(config_file)

    assert config.host == "http://example.test"
    assert config.request_timeout_seconds is None
    assert config.wait_time_min_seconds == 0


def test_request_timeout_can_be_overridden_by_env(monkeypatch, tmp_path: Path) -> None:
    """请求超时可以通过环境变量临时覆盖。"""

    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[runtime]\n"
        "request_timeout_seconds = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCUST_REQUEST_TIMEOUT_SECONDS", "12.5")

    config = load_runtime_config(config_file)

    assert config.request_timeout_seconds == 12.5
