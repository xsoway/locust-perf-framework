#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""项目配置读取工具。"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    """压测运行配置。"""

    host: str
    environment: str
    request_timeout_seconds: float | None
    wait_time_min_seconds: float
    wait_time_max_seconds: float
    log_level: str
    log_dir: str


def _optional_timeout(value: str) -> float | None:
    timeout = float(value)
    return None if timeout <= 0 else timeout


def _runtime_value(runtime: configparser.SectionProxy | dict[str, str], key: str, default: str) -> str:
    """读取运行配置，并允许同名 LOCUST 环境变量覆盖。"""

    env_key = f"LOCUST_{key.upper()}"
    return os.getenv(env_key, runtime.get(key, default))


def load_runtime_config(config_file: Path | str = "config/config.ini") -> RuntimeConfig:
    """读取运行配置，并允许环境变量覆盖 host。"""

    parser = configparser.ConfigParser()
    parser.read(config_file, encoding="utf-8")

    runtime = parser["runtime"] if parser.has_section("runtime") else {}
    logging_config = parser["logging"] if parser.has_section("logging") else {}

    return RuntimeConfig(
        host=os.getenv("LOCUST_TARGET_HOST", runtime.get("host", "http://localhost")),
        environment=runtime.get("environment", "local"),
        request_timeout_seconds=_optional_timeout(
            _runtime_value(runtime, "request_timeout_seconds", "10")
        ),
        wait_time_min_seconds=float(runtime.get("wait_time_min_seconds", "1")),
        wait_time_max_seconds=float(runtime.get("wait_time_max_seconds", "2")),
        log_level=logging_config.get("level", "INFO"),
        log_dir=logging_config.get("log_dir", "logs"),
    )
