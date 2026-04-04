from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ISCC = Path(
    "C:/Users/larbi/AppData/Local/Programs/Antigravity/resources/app/node_modules/innosetup/bin/ISCC.exe"
)
INSTALLER_SCRIPT = ROOT / "installer" / "forge.iss"
OUTPUT_DIR = ROOT / "release-assets" / "installer-output"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(">", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=cwd or ROOT, check=True)


def ensure_prerequisites() -> None:
    if not ISCC.exists():
        raise FileNotFoundError(f"ISCC.exe not found at {ISCC}")
    run(["python", str(ROOT / "tools" / "build_installer_assets.py")], cwd=ROOT)


def build_installer() -> Path:
    ensure_prerequisites()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run([str(ISCC), str(INSTALLER_SCRIPT)], cwd=ROOT / "installer")
    candidates = sorted(OUTPUT_DIR.glob("FORGE-Setup-*.exe"))
    if not candidates:
        raise FileNotFoundError("Inno Setup did not produce an installer executable.")
    return candidates[-1]


def main() -> None:
    installer = build_installer()
    print(f"Built installer: {installer}")


if __name__ == "__main__":
    main()
