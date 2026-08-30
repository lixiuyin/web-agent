"""Tests for logging configuration."""

from __future__ import annotations

import logging

from webagent.utils.logging import configure_logging


def test_configure_logging_adds_single_handler():
    logger = logging.getLogger("webagent")
    logger.handlers.clear()
    try:
        configure_logging(level=logging.DEBUG)
        assert len(logger.handlers) == 1
        assert logger.level == logging.DEBUG
        # Idempotent: a second call must not add another handler.
        configure_logging()
        assert len(logger.handlers) == 1
    finally:
        logger.handlers.clear()
