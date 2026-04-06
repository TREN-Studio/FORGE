from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    exe_path = Path(os.environ.get("FORGE_DESKTOP_BINARY_PATH", str(ROOT / "dist" / "FORGE-Desktop.exe"))).resolve()
    if not exe_path.exists():
        raise FileNotFoundError(f"Desktop binary is missing: {exe_path}")

    command = [
        str(exe_path),
        "--host",
        os.environ.get("FORGE_DESKTOP_TEST_HOST", "127.0.0.1"),
        "--port",
        os.environ.get("FORGE_DESKTOP_TEST_PORT", "43019"),
        "--no-browser",
    ]
    child = subprocess.Popen(command, cwd=ROOT)

    def shutdown(*_args: object) -> None:
        if child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    atexit.register(shutdown)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)

    try:
        while child.poll() is None:
            time.sleep(0.5)
    finally:
        shutdown()
    return int(child.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
