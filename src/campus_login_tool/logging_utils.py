"""Logging helpers for the campus login CLI."""

import logging
from pathlib import Path
from urllib.parse import urlparse


def setup_logging(verbose: bool = False, log_file: str | None = None) -> logging.Logger:
    """Create the application logger."""
    logger = logging.getLogger("campus_login")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    return logger


def mask_value(value: str, keep_start: int = 2, keep_end: int = 2) -> str:
    """Return a minimally identifying masked value for logs."""
    if not value:
        return "<empty>"

    if len(value) <= keep_start + keep_end:
        return "*" * len(value)

    hidden = "*" * (len(value) - keep_start - keep_end)
    return f"{value[:keep_start]}{hidden}{value[-keep_end:]}"


def sanitize_url(url: str) -> str:
    """Drop query fragments before logging URLs."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def truncate_text(content: str, limit: int = 160) -> str:
    """Shorten long debug strings to avoid noisy logs."""
    if len(content) <= limit:
        return content
    return f"{content[:limit]}..."
