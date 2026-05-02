"""
utils/logger.py
─────────────────────────────────────────────────────────────────────────────
Centralised Loguru logger used across the entire project.
Usage:
    from utils.logger import log
    log.info("message")
─────────────────────────────────────────────────────────────────────────────
"""
import sys
import os
from loguru import logger as log

from config import LOG_DIR, LOG_LEVEL, LOG_ROTATION, LOG_RETENTION

os.makedirs(LOG_DIR, exist_ok=True)

# Remove default handler
log.remove()

# Console — colourful, human-readable
log.add(
    sys.stdout,
    level=LOG_LEVEL,
    colorize=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>",
)

# File — full structured log
log.add(
    os.path.join(LOG_DIR, "scanner_{time:YYYY-MM-DD}.log"),
    level="DEBUG",
    rotation=LOG_ROTATION,
    retention=LOG_RETENTION,
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} — {message}",
)

__all__ = ["log"]
