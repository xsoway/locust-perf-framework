#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日志初始化工具。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def get_logger(name: str, log_dir: Path | str = "logs") -> logging.Logger:
    """创建同时输出到控制台和文件的 logger。"""

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    module_name = name.split(".")[-1] or "app"
    log_file = Path(log_dir) / f"{module_name}_{datetime.now().strftime('%Y%m%d')}.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger
