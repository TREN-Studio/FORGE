from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = ROOT / "installer" / "forge.iss"
OUTPUT_DIR = ROOT / "release-assets" / "installer-output"
ISCC_CANDIDATES = [
    lambda: Path(os.environ["ISCC_EXE"]).expanduser(),
    lambda: Path(os.environ["FORGE_ISCC_EXE"]).expanduser(),
    lambda: Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
    lambda: Path("C:/Users/larbi/AppData/Local/Programs/Antigravity/resources/app/node_modules/innosetup/bin/ISCC.exe"),
]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(">", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=cwd or ROOT, check=True)


def find_iscc() -> Path:
    for candidate_factory in ISCC_CANDIDATES:
        try:
            candidate = candidate_factory()
        except KeyError:
            continue
        if candidate.exists():
            return candidate
    checked = []
    for candidate_factory in ISCC_CANDIDATES:
        try:
            checked.append(str(candidate_factory()))
        except KeyError:
            continue
    raise FileNotFoundError(
        "ISCC.exe not found. Checked: " + ", ".join(checked)
    )


def ensure_prerequisites() -> Path:
    iscc = find_iscc()
    run(["python", str(ROOT / "tools" / "build_installer_assets.py")], cwd=ROOT)
    return iscc


def build_installer() -> Path:
    iscc = ensure_prerequisites()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run([str(iscc), str(INSTALLER_SCRIPT)], cwd=ROOT / "installer")
    candidates = sorted(OUTPUT_DIR.glob("FORGE-Setup-*.exe"))
    if not candidates:
        raise FileNotFoundError("Inno Setup did not produce an installer executable.")
    return candidates[-1]


def main() -> None:
    installer = build_installer()
    print(f"Built installer: {installer}")


if __name__ == "__main__":
    main()
