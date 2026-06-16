"""Logging configuration for the webagent package."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Set up a simple console + file logger for webagent."""
    logger = logging.getLogger("webagent")
    if logger.handlers:
        return
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
