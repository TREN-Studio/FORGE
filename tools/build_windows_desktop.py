from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
TEMP_DIST = ROOT / ".build-dist"
TEMP_BUILD = ROOT / ".build-work"
ICON = ROOT / "assets" / "forge-desktop-icon.ico"
SPEC_FILE = ROOT / "FORGE-Desktop.spec"


def run(cmd: list[str]) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _finalize_binary(built_exe: Path) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    primary = DIST / "FORGE-Desktop.exe"
    try:
        if primary.exists():
            primary.unlink()
        shutil.copy2(built_exe, primary)
        return primary
    except PermissionError:
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        fallback = DIST / f"FORGE-Desktop-{timestamp}.exe"
        shutil.copy2(built_exe, fallback)
        return fallback


def main() -> None:
    if not ICON.exists():
        run([sys.executable, str(ROOT / "tools" / "build_forge_icon.py")])
    if not SPEC_FILE.exists():
        raise FileNotFoundError(f"Spec file is missing: {SPEC_FILE}")

    _safe_rmtree(TEMP_DIST)
    _safe_rmtree(TEMP_BUILD)
    _safe_rmtree(BUILD)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(TEMP_DIST),
        "--workpath",
        str(TEMP_BUILD),
        str(SPEC_FILE),
    ]
    run(cmd)

    exe = TEMP_DIST / "FORGE-Desktop.exe"
    if not exe.exists():
        raise FileNotFoundError(f"Build succeeded but {exe} is missing.")
    final_exe = _finalize_binary(exe)
    print(f"Built {final_exe}")


if __name__ == "__main__":
    main()
