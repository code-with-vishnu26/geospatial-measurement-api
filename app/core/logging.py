"""Logging configuration.

Log records are emitted as single-line ``key=value`` pairs so they stay
readable locally and remain easy to parse by log shippers.
"""

import logging
import sys

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if not extras:
            return base
        return base + " " + " ".join(f"{key}={value}" for key, value in extras.items())


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        KeyValueFormatter('%(asctime)s level=%(levelname)s logger=%(name)s msg="%(message)s"')
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
