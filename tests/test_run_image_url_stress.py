#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图片 URL 一体化执行入口测试。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from tools.run_image_url_stress import _derive_url_scope, _run_locust


def test_derive_url_scope_from_first_valid_url(tmp_path: Path) -> None:
    """可以从 URL 文件首条有效数据推导目标域名和路径。"""

    url_file = tmp_path / "02-url.txt"
    url_file.write_text(
        "# comment\n"
        "https://cdn.example.test/images/a.jpg?version=1\n"
        "https://cdn.example.test/images/b.jpg\n",
        encoding="utf-8",
    )

    host, path = _derive_url_scope(str(url_file))

    assert host == "cdn.example.test"
    assert path == "/images/a.jpg"


def test_run_locust_returns_nonzero_without_raising(
    monkeypatch, tmp_path: Path
) -> None:
    """Locust 返回失败码时，入口应保留后续生成中文报告的机会。"""

    captured: dict[str, object] = {}

    def fake_run(command, check, env):  # noqa: ANN001
        captured["command"] = command
        captured["check"] = check
        captured["env"] = env
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    args = argparse.Namespace(
        users=100,
        spawn_rate=5,
        url_file="data/01-url.txt",
        url_limit=2000,
        max_time="",
        transport="requests",
        read_mode="first_chunk",
    )

    return_code = _run_locust(
        tmp_path,
        tmp_path / "summary.json",
        tmp_path / "failure_details.jsonl",
        tmp_path / "request_details.jsonl",
        tmp_path / "downloaded_images",
        tmp_path / "download_results.jsonl",
        args,
    )

    assert return_code == 1
    assert captured["check"] is False
    assert captured["command"][0] == sys.executable
    assert captured["env"]["LOCUST_IMAGE_URL_LIMIT"] == "2000"
    assert captured["env"]["LOCUST_IMAGE_FAILURE_DETAILS_FILE"].endswith("failure_details.jsonl")
    assert captured["env"]["LOCUST_IMAGE_REQUEST_LOG_FILE"].endswith("request_details.jsonl")
    assert captured["env"]["LOCUST_IMAGE_DOWNLOAD_DIR"].endswith("downloaded_images")
    assert captured["env"]["LOCUST_IMAGE_DOWNLOAD_LOG_FILE"].endswith("download_results.jsonl")
    assert captured["env"]["LOCUST_IMAGE_TRANSPORT"] == "requests"
    assert captured["env"]["LOCUST_IMAGE_READ_MODE"] == "first_chunk"
