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
    env = os.environ.copy()
    env.setdefault("FORGE_PORTAL_API_BASE_URL", "http://127.0.0.1:43017/api/index.php")

    command = [
        sys.executable,
        str(ROOT / "forge_desktop.py"),
        "--host",
        env.get("FORGE_DESKTOP_TEST_HOST", "127.0.0.1"),
        "--port",
        env.get("FORGE_DESKTOP_TEST_PORT", "43018"),
        "--no-browser",
    ]
    child = subprocess.Popen(command, cwd=ROOT, env=env)

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
