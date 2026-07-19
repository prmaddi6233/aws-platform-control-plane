"""Structured JSON logging with correlation IDs."""

import logging
import sys
from typing import Any

import structlog

# Initialize structlog once
_INITIALIZED = False


def setup_logging(log_level: int = logging.INFO) -> None:
    """Configure structlog to output JSON with correlation IDs.

    This configures both the standard library logging and structlog to ensure
    all logs (including from third-party libraries like boto3) are formatted
    consistently as JSON.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    # Processors applied to all log messages
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to pipe through structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    # Logs go to stderr so stdout stays clean for machine-readable CLI output.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Quiet down noisy boto3 logs
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    _INITIALIZED = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound with the given name."""
    if not _INITIALIZED:
        setup_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_context(**kwargs: Any) -> None:
    """Bind context variables (like request_id) to all subsequent log lines."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear bound context variables."""
    structlog.contextvars.clear_contextvars()
