from __future__ import annotations

import fnmatch
import shutil
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ASSETS = ROOT / "release-assets"
SITE_DOWNLOADS = ROOT / "site" / "downloads"
DIST_EXE = ROOT / "dist" / "FORGE-Desktop.exe"

INCLUDE_DIRS = [
    ".github",
    "assets",
    "forge",
    "installer",
    "site",
    "site_backend",
    "site_extensions",
    "tests",
    "tools",
]

ROOT_FILE_PATTERNS = [
    "*.md",
    "*.py",
    "*.spec",
    "*.toml",
    "*.json",
    "*.js",
    "*.txt",
    "LICENSE",
]

EXCLUDE_PARTS = {
    ".git",
    ".build-dist",
    ".build-work",
    ".forge_artifacts",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "output",
    "release-assets",
    "test-results",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".sqlite3",
}

EXCLUDE_FILES = {
    ".portal-dev.err.log",
    ".portal-dev.out.log",
}
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _safe_rel(path: Path) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {path}") from exc


def _is_excluded(path: Path) -> bool:
    rel = _safe_rel(path)
    parts = set(rel.parts)
    if parts & EXCLUDE_PARTS:
        return True
    if len(rel.parts) >= 2 and rel.parts[0] == "site" and rel.parts[1] == "downloads":
        return True
    if path.name in EXCLUDE_FILES:
        return True
    return path.suffix.lower() in EXCLUDE_SUFFIXES


def _iter_source_files() -> list[Path]:
    files: list[Path] = []

    for directory_name in INCLUDE_DIRS:
        directory = ROOT / directory_name
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and not _is_excluded(path):
                files.append(path)

    for path in ROOT.iterdir():
        if not path.is_file() or _is_excluded(path):
            continue
        if any(fnmatch.fnmatch(path.name, pattern) for pattern in ROOT_FILE_PATTERNS):
            files.append(path)

    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix().lower())


def _zip_files(destination: Path, files: list[Path], prefix: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            archive_name = f"{prefix}/{rel}" if prefix else rel
            _write_zip_entry(archive, path, archive_name)


def _write_zip_entry(archive: zipfile.ZipFile, path: Path, archive_name: str) -> None:
    info = zipfile.ZipInfo(archive_name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16
    archive.writestr(info, path.read_bytes())


def package_portable(version: str) -> Path:
    if not DIST_EXE.exists():
        raise FileNotFoundError(f"Desktop binary is missing: {DIST_EXE}")

    portable_dir = RELEASE_ASSETS / "portable" / f"FORGE-Windows-Portable-{version}"
    portable_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DIST_EXE, portable_dir / "FORGE-Desktop.exe")
    (portable_dir / "README.txt").write_text(
        f"FORGE Windows Portable {version}\n\n"
        "This portable build contains the desktop binary.\n"
        "Use the Portal onboarding flow, then launch FORGE-Desktop.exe.\n\n"
        "Known limitation:\n"
        "- Windows SmartScreen may still warn if the binary is unsigned.\n",
        encoding="utf-8",
    )

    destination = RELEASE_ASSETS / f"FORGE-Windows-Portable-{version}.zip"
    files = sorted(path for path in portable_dir.rglob("*") if path.is_file())
    if destination.exists():
        destination.unlink()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            _write_zip_entry(archive, path, path.relative_to(portable_dir).as_posix())
    return destination


def package_source(version: str) -> Path:
    destination = RELEASE_ASSETS / f"FORGE-Source-v{version}.zip"
    _zip_files(destination, _iter_source_files(), prefix=f"FORGE-Source-v{version}")
    return destination


def sync_site_downloads(version: str, portable_zip: Path, source_zip: Path) -> dict[str, Path]:
    SITE_DOWNLOADS.mkdir(parents=True, exist_ok=True)
    installer = RELEASE_ASSETS / "installer-output" / f"FORGE-Setup-{version}.exe"
    if not installer.exists():
        raise FileNotFoundError(f"Installer is missing: {installer}")

    outputs = {
        "FORGE-Desktop.exe": DIST_EXE,
        f"FORGE-Setup-{version}.exe": SITE_DOWNLOADS / f"FORGE-Setup-{version}.exe",
        f"FORGE-Windows-Portable-{version}.zip": SITE_DOWNLOADS / f"FORGE-Windows-Portable-{version}.zip",
        "FORGE-Windows-Desktop.zip": SITE_DOWNLOADS / "FORGE-Windows-Desktop.zip",
        f"FORGE-Source-v{version}.zip": SITE_DOWNLOADS / f"FORGE-Source-v{version}.zip",
    }

    shutil.copy2(installer, outputs[f"FORGE-Setup-{version}.exe"])
    shutil.copy2(portable_zip, outputs[f"FORGE-Windows-Portable-{version}.zip"])
    shutil.copy2(portable_zip, outputs["FORGE-Windows-Desktop.zip"])
    shutil.copy2(source_zip, outputs[f"FORGE-Source-v{version}.zip"])
    return outputs


def write_sha256(version: str, outputs: dict[str, Path]) -> Path:
    import hashlib

    lines: list[str] = []
    for name, path in outputs.items():
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")

    checksum = RELEASE_ASSETS / f"SHA256SUMS-{version}.txt"
    checksum.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copy2(checksum, SITE_DOWNLOADS / checksum.name)
    return checksum


def main() -> None:
    version = _version()
    portable_zip = package_portable(version)
    source_zip = package_source(version)
    outputs = sync_site_downloads(version, portable_zip, source_zip)
    checksum = write_sha256(version, outputs)

    print(f"Packaged {portable_zip}")
    print(f"Packaged {source_zip}")
    print(f"Wrote {checksum}")


if __name__ == "__main__":
    main()
