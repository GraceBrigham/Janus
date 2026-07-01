from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

_log_path: Optional[str] = None


def init(log_path: Optional[str]) -> None:
    """
    Initialize logging output. If log_path is None, logging is disabled.
    """
    global _log_path
    _log_path = log_path
    if not _log_path:
        return
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
    with open(_log_path, "w", encoding="utf-8") as handle:
        handle.write(f"# Run log started {datetime.utcnow().isoformat()}Z\n")


def log(message: str) -> None:
    if not _log_path:
        return
    timestamp = datetime.utcnow().isoformat() + "Z"
    with open(_log_path, "a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def path() -> Optional[str]:
    return _log_path
