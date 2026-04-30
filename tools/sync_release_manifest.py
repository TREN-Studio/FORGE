from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPOSITORY = "TREN-Studio/FORGE"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the public site release manifest from the canonical GitHub Release."
    )
    parser.add_argument("--tag", required=True, help="Release tag, for example v1.1.4")
    parser.add_argument("--output", default="site/release-manifest.json")
    parser.add_argument(
        "--mirror-base-url",
        default="",
        help="Optional official site mirror base URL, for example https://www.trenstudio.com/FORGE/downloads",
    )
    return parser


def fetch_release(tag: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{GITHUB_API}/releases/tags/{tag}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "FORGE-release-manifest-sync",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def manifest_from_release(release: dict[str, Any], mirror_base_url: str = "") -> dict[str, Any]:
    tag = str(release["tag_name"])
    version = tag.removeprefix("v")
    mirror_base_url = mirror_base_url.strip().rstrip("/")
    assets = sorted(release.get("assets", []), key=lambda item: str(item.get("name", "")).lower())
    return {
        "schema_version": 1,
        "version": version,
        "release_tag": tag,
        "release_url": str(release["html_url"]),
        "canonical_host": "github_release",
        "canonical_note": (
            "GitHub Release is the canonical release record. "
            "Hostinger may mirror the same release assets byte-for-byte when mirror_url is present."
        ),
        "assets": [_asset_from_github(version, tag, asset, mirror_base_url) for asset in assets],
        "mirrors": {
            "hostinger": {
                "status": "enabled" if mirror_base_url else "disabled",
                "base_url": mirror_base_url or None,
                "identity_policy": "mirror_url assets must match canonical_url by version, file size, and SHA256.",
            }
        },
    }


def _asset_from_github(version: str, tag: str, asset: dict[str, Any], mirror_base_url: str) -> dict[str, Any]:
    name = str(asset["name"])
    digest = str(asset.get("digest") or "")
    sha256 = digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""
    return {
        "name": name,
        "label": _asset_label(version, name),
        "kind": _asset_kind(name),
        "platform": _asset_platform(name),
        "sha256": sha256,
        "size": int(asset["size"]),
        "canonical_url": str(asset.get("browser_download_url") or _download_url(tag, name)),
        "mirror_url": f"{mirror_base_url}/{name}" if mirror_base_url else None,
    }


def _download_url(tag: str, name: str) -> str:
    return f"https://github.com/{GITHUB_REPOSITORY}/releases/download/{tag}/{name}"


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
    args = build_parser().parse_args()
    release = fetch_release(args.tag)
    if release.get("draft"):
        raise RuntimeError(f"Release {args.tag} is still a draft.")
    manifest = manifest_from_release(release, mirror_base_url=args.mirror_base_url)
    destination = (ROOT / args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {destination}")


if __name__ == "__main__":
    main()
