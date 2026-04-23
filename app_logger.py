# -*- coding: utf-8 -*-
"""
统一日志模块
---------------------------------
- 文件：logs/keyboard_lock.log（按天滚动，保留 7 天）
- 同时输出到 stderr
- 使用：from app_logger import log；log.info("xxx")
"""

import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_HERE, "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "keyboard_lock.log")


def _build_logger():
    os.makedirs(_LOG_DIR, exist_ok=True)

    logger = logging.getLogger("KeyboardLocker")
    if logger.handlers:
        # 已初始化过，直接复用
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(module)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件：按天滚动，保留 7 天
    try:
        fh = TimedRotatingFileHandler(
            _LOG_PATH,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
            delay=True,
        )
        fh.setFormatter(fmt)
        fh.setLevel(logging.INFO)
        logger.addHandler(fh)
    except Exception as e:
        # 文件日志失败也不影响程序运行
        sys.stderr.write(f"[logger] 文件日志初始化失败: {e}\n")

    # 控制台（stderr）
    try:
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setFormatter(fmt)
        ch.setLevel(logging.INFO)
        logger.addHandler(ch)
    except Exception:
        pass

    return logger


log = _build_logger()

# 便捷快捷方式
def info(msg, *a, **kw):    log.info(msg, *a, **kw)
def warning(msg, *a, **kw): log.warning(msg, *a, **kw)
def error(msg, *a, **kw):   log.error(msg, *a, **kw)
def exception(msg, *a, **kw): log.exception(msg, *a, **kw)


def get_log_path():
    return _LOG_PATH
