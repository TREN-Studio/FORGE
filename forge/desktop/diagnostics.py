from __future__ import annotations

import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path


LOG_PATH = Path(tempfile.gettempdir()) / "forge-desktop.log"


def log_event(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def log_exception(prefix: str, exc: BaseException) -> None:
    log_event(f"{prefix}: {exc}")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(traceback.format_exc())
        handle.write("\n")
