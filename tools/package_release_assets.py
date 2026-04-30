from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ASSETS = ROOT / "release-assets"
DIST_EXE = ROOT / "dist" / "FORGE-Desktop.exe"
GITHUB_REPOSITORY = "TREN-Studio/FORGE"

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


def release_outputs(version: str, portable_zip: Path, source_zip: Path) -> dict[str, Path]:
    installer = RELEASE_ASSETS / "installer-output" / f"FORGE-Setup-{version}.exe"
    if not installer.exists():
        raise FileNotFoundError(f"Installer is missing: {installer}")

    desktop_asset = RELEASE_ASSETS / "FORGE-Desktop.exe"
    installer_asset = RELEASE_ASSETS / f"FORGE-Setup-{version}.exe"
    shutil.copy2(DIST_EXE, desktop_asset)
    shutil.copy2(installer, installer_asset)

    return {
        "FORGE-Desktop.exe": desktop_asset,
        f"FORGE-Setup-{version}.exe": installer_asset,
        f"FORGE-Windows-Portable-{version}.zip": portable_zip,
        f"FORGE-Source-v{version}.zip": source_zip,
    }


def write_sha256(version: str, outputs: dict[str, Path]) -> Path:
    lines: list[str] = []
    for name, path in outputs.items():
        if not path.exists():
            continue
        digest = _sha256(path)
        lines.append(f"{digest}  {name}")

    checksum = RELEASE_ASSETS / f"SHA256SUMS-{version}.txt"
    checksum.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum


def write_release_manifest(version: str, outputs: dict[str, Path], checksum: Path) -> Path:
    tag = f"v{version}"
    all_outputs = dict(outputs)
    all_outputs[checksum.name] = checksum
    mirror_base_url = os.getenv("FORGE_RELEASE_MIRROR_BASE_URL", "").strip().rstrip("/")
    assets = [
        _manifest_asset(
            version=version,
            tag=tag,
            name=name,
            path=path,
            mirror_base_url=mirror_base_url,
        )
        for name, path in all_outputs.items()
    ]
    manifest = {
        "schema_version": 1,
        "version": version,
        "release_tag": tag,
        "release_url": f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/{tag}",
        "canonical_host": "github_release",
        "canonical_note": "GitHub Release is the canonical release record. Hostinger may mirror the same release assets byte-for-byte when mirror_url is present.",
        "mirrors": {
            "hostinger": {
                "status": "enabled" if mirror_base_url else "disabled",
                "base_url": mirror_base_url or None,
                "identity_policy": "mirror_url assets must match canonical_url by version, file size, and SHA256.",
            }
        },
        "assets": assets,
    }
    destination = RELEASE_ASSETS / "release-manifest.json"
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _manifest_asset(*, version: str, tag: str, name: str, path: Path, mirror_base_url: str) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Release asset is missing: {path}")
    return {
        "name": name,
        "label": _asset_label(version, name),
        "kind": _asset_kind(name),
        "platform": _asset_platform(name),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
        "canonical_url": f"https://github.com/{GITHUB_REPOSITORY}/releases/download/{tag}/{name}",
        "mirror_url": f"{mirror_base_url}/{name}" if mirror_base_url else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_label(version: str, name: str) -> str:
    labels = {
        "FORGE-Desktop.exe": "Windows Desktop Executable",
        f"FORGE-Setup-{version}.exe": "Windows Installer",
        f"FORGE-Windows-Portable-{version}.zip": "Windows Portable ZIP",
        f"FORGE-Source-v{version}.zip": "Source Archive",
        f"SHA256SUMS-{version}.txt": "SHA256 Checksums",
    }
    return labels.get(name, name)


def _asset_kind(name: str) -> str:
    lowered = name.lower()
    if "setup" in lowered:
        return "installer"
    if "portable" in lowered:
        return "portable"
    if "source" in lowered:
        return "source"
    if "sha256" in lowered:
        return "checksums"
    if lowered.endswith(".exe"):
        return "executable"
    return "asset"


def _asset_platform(name: str) -> str:
    lowered = name.lower()
    if "windows" in lowered or lowered.endswith(".exe"):
        return "windows"
    if "source" in lowered or "sha256" in lowered:
        return "all"
    return "unknown"


def main() -> None:
    version = _version()
    portable_zip = package_portable(version)
    source_zip = package_source(version)
    outputs = release_outputs(version, portable_zip, source_zip)
    checksum = write_sha256(version, outputs)
    manifest = write_release_manifest(version, outputs, checksum)

    print(f"Packaged {portable_zip}")
    print(f"Packaged {source_zip}")
    print(f"Wrote {checksum}")
    print(f"Wrote {manifest}")


if __name__ == "__main__":
    main()
