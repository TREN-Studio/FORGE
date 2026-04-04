from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON = ROOT / "assets" / "forge-desktop-icon.ico"
ENTRYPOINT = ROOT / "forge_desktop.py"


def run(cmd: list[str]) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> None:
    if not ICON.exists():
        run([sys.executable, str(ROOT / "tools" / "build_forge_icon.py")])

    if DIST.exists():
        shutil.rmtree(DIST)
    if BUILD.exists():
        shutil.rmtree(BUILD)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "FORGE-Desktop",
        "--icon",
        str(ICON),
        "--hidden-import",
        "forge.providers.groq",
        "--hidden-import",
        "forge.providers.gemini",
        "--hidden-import",
        "forge.providers.ollama",
        "--hidden-import",
        "forge.providers.deepseek",
        "--hidden-import",
        "forge.providers.openrouter",
        "--add-data",
        f"{ROOT / 'forge' / 'skills_catalog'};forge/skills_catalog",
        str(ENTRYPOINT),
    ]
    run(cmd)

    exe = DIST / "FORGE-Desktop.exe"
    if not exe.exists():
        raise FileNotFoundError(f"Build succeeded but {exe} is missing.")
    print(f"Built {exe}")


if __name__ == "__main__":
    main()
