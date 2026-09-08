#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time     : 2026/07/22 15:30
# @Filename : locust_taoche_browse_stress.py
# @Author   : Alan_Hsu
"""淘车车二手车浏览场景压力测试脚本。

流量模型：加权随机（首页 10%、全部列表 20%、品牌筛选 10%、多条件筛选 20%、
           详情页 20%、参数配置 10%、城市切换 10%）。
"""

from __future__ import annotations

import csv
import json
import os
import random
from pathlib import Path

from locust import HttpUser, between, events, task

from tools.logging_setup import get_logger

# ---------- 常量 ----------

TARGET_HOST = os.getenv(
    "LOCUST_TARGET_HOST",
    "https://example.com",
)
_URL_FILE_ENV = os.getenv("LOCUST_TAOCHE_URL_FILE", "")
DEFAULT_URL_FILE = (
    Path(_URL_FILE_ENV) if _URL_FILE_ENV else Path("data/examples/taoche_browse_urls.csv")
)
FAILURE_DETAILS_FILE = os.getenv("LOCUST_TAOCHE_FAILURE_DETAILS_FILE", "")
SUMMARY_FILE = os.getenv("LOCUST_TAOCHE_SUMMARY_FILE", "")

# ---------- 工具函数 ----------


def get_script_logger():
    """获取脚本 logger，日志写入 logs/ 目录。"""
    return get_logger("locust_taoche_browse_stress")


def load_urls(csv_file: Path) -> list[dict[str, str]]:
    """从 CSV 数据文件加载 URL 配置，返回 [{page_type, url, weight, label}]。"""
    if not csv_file.exists():
        raise FileNotFoundError(f"URL 数据文件不存在: {csv_file}")

    rows: list[dict[str, str]] = []
    with csv_file.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV 文件为空或格式错误: {csv_file}")
        expected = {"page_type", "url", "weight", "label"}
        missing = expected - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV 文件缺少列: {missing}，文件: {csv_file}")
        for row in reader:
            rows.append(row)

    if not rows:
        raise ValueError(f"CSV 文件无有效数据行: {csv_file}")

    get_script_logger().info(
        "已加载 %s 条 URL 配置, 文件: %s",
        len(rows),
        csv_file,
    )
    return rows


def _write_jsonl(file_path: str, record: dict[str, object]) -> None:
    """追加一条 JSONL 记录到指定文件。"""
    if not file_path:
        return
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        get_script_logger().error("写入 JSONL 失败 file=%s error=%s", file_path, exc)


# ---------- 全局状态 ----------

url_rows = load_urls(DEFAULT_URL_FILE)  # [{page_type, url, weight, label}]

# 按 page_type 分组，方便任务函数引用
_url_by_type: dict[str, dict[str, str]] = {}
for row in url_rows:
    _url_by_type[row["page_type"]] = row

# 加权随机选择的权重列表（动态构建，支持两套 URL 模式）
_url_weights = [int(row["weight"]) for row in url_rows]

# 请求计数（仅用于日志）
_request_counter: dict[str, int] = {}
_request_total = 0


def _make_failure_detail(
    page_type: str,
    url: str,
    label: str,
    status_code: int,
    response_body: str,
) -> dict[str, object]:
    """构造失败明细记录。"""
    return {
        "page_type": page_type,
        "url": url,
        "label": label,
        "status_code": status_code,
        "response_snippet": response_body[:500],
    }


def _build_summary() -> dict[str, object]:
    """构造压测辅助统计。"""
    return {
        "total_url_types": len(url_rows),
        "request_counts": dict(_request_counter),
        "request_total": _request_total,
    }


def _write_summary() -> None:
    """将压测辅助统计写入 SUMMARY_FILE。"""
    if not SUMMARY_FILE:
        return
    try:
        path = Path(SUMMARY_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_build_summary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        get_script_logger().error("写入摘要失败 file=%s error=%s", SUMMARY_FILE, exc)


# ---------- Locust 用户 ----------


class TaocheBrowseUser(HttpUser):
    """淘车车二手车浏览用户：按 CSV 中 weight 列的加权随机模式访问各类型页面。"""

    host = TARGET_HOST
    wait_time = between(1, 3)  # 用户浏览间隔 1-3 秒

    @task
    def visit_random_page(self) -> None:
        """根据 CSV 中 weight 列按加权随机模式访问一个页面。"""
        if not url_rows:
            return
        row = random.choices(url_rows, weights=_url_weights, k=1)[0]
        self._do_request(row["page_type"])

    def _do_request(self, page_type: str) -> None:
        """通用的请求处理逻辑：按 page_type 查 URL → 发 GET → 校验状态码。"""
        row = _url_by_type.get(page_type)
        if row is None:
            get_script_logger().warning("未知 page_type=%s，跳过请求", page_type)
            return

        global _request_total  # noqa: PLW0603
        _request_total += 1
        _request_counter[page_type] = _request_counter.get(page_type, 0) + 1

        url: str = row["url"]
        label: str = row["label"]

        with self.client.get(
            url,
            name=label,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                failure_reason = (
                    f"请求失败 status={response.status_code} "
                    f"page_type={page_type}"
                )
                response.failure(failure_reason)
                _write_jsonl(
                    FAILURE_DETAILS_FILE,
                    _make_failure_detail(
                        page_type,
                        url,
                        label,
                        response.status_code,
                        response.text[:500],
                    ),
                )
                return
            response.success()


# ---------- Locust 事件回调 ----------


@events.init.add_listener
def on_locust_init(environment, **kwargs) -> None:  # noqa: ANN001
    """Locust 初始化时记录场景配置信息。"""
    get_script_logger().info(
        "淘车车浏览压测脚本加载完成 "
        "host=%s url_types=%s wait_time=1-3s",
        TARGET_HOST,
        len(url_rows),
    )


@events.quitting.add_listener
def on_locust_quitting(environment, **kwargs) -> None:  # noqa: ANN001
    """Locust 退出时输出统计摘要。"""
    _write_summary()
    summary = _build_summary()
    get_script_logger().info(
        "淘车车浏览压测结束 summary=%s total_fail_ratio=%.4f",
        json.dumps(summary, ensure_ascii=False),
        environment.stats.total.fail_ratio,
    )
