"""Logging configuration shared by every entry point."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once; safe to call repeatedly.

    Callers must never pass secret values to log messages. Settings hide
    secrets through SecretStr, but raw strings are not protected.
    """
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
