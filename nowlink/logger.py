# nowlink/logger.py
# File-based logging for all tool calls.
# Logs go to ~/.nowlink/logs/YYYY-MM-DD.log
# One line per call: timestamp | tool | params summary | result summary

import json
import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".nowlink" / "logs"


def _get_logger() -> logging.Logger:
    """Return a logger writing to today's log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{today}.log"

    logger = logging.getLogger("nowlink")
    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


def log_tool_call(tool: str, params: dict, result_summary: str):
    """Log a tool call with its parameters and a short result summary."""
    logger = _get_logger()
    params_str = json.dumps(params, ensure_ascii=False)
    logger.info(f"{tool} | params={params_str} | result={result_summary}")


def log_error(tool: str, params: dict, error: str):
    """Log a tool call that resulted in an error."""
    logger = _get_logger()
    params_str = json.dumps(params, ensure_ascii=False)
    logger.error(f"{tool} | params={params_str} | ERROR={error}")
