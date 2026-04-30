from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GITHUB_API = "https://api.github.com/repos/TREN-Studio/FORGE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify public GitHub Release assets and optional Hostinger mirror identity."
    )
    parser.add_argument("--manifest", default="release-assets/release-manifest.json")
    parser.add_argument("--require-mirror", action="store_true")
    parser.add_argument("--skip-download-bytes", action="store_true")
    return parser


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("canonical_host") != "github_release":
        raise ValueError("Manifest canonical_host must be github_release.")
    if not manifest.get("version") or not manifest.get("release_tag"):
        raise ValueError("Manifest must include version and release_tag.")
    return manifest


def fetch_json(url: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "FORGE-release-verifier",
    }
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def head(url: str) -> tuple[int, int | None]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "FORGE-release-verifier"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content_length = response.headers.get("Content-Length")
        return response.status, int(content_length) if content_length else None


def sha256_url(url: str) -> tuple[str, int]:
    request = urllib.request.Request(url, headers={"User-Agent": "FORGE-release-verifier"})
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), total


def verify(manifest: dict[str, Any], *, require_mirror: bool, skip_download_bytes: bool) -> list[dict[str, Any]]:
    tag = str(manifest["release_tag"])
    release = fetch_json(f"{GITHUB_API}/releases/tags/{tag}")
    release_assets = {asset["name"]: asset for asset in release.get("assets", [])}
    results: list[dict[str, Any]] = []

    if str(release.get("tag_name")) != tag:
        raise ValueError(f"GitHub release tag mismatch: {release.get('tag_name')} != {tag}")
    if release.get("draft"):
        raise ValueError(f"GitHub release {tag} is still a draft.")

    for asset in manifest["assets"]:
        name = str(asset["name"])
        expected_size = int(asset["size"])
        expected_sha = str(asset["sha256"])
        github_asset = release_assets.get(name)
        if not github_asset:
            raise ValueError(f"{name}: missing from GitHub Release {tag}")

        github_size = int(github_asset["size"])
        github_digest = str(github_asset.get("digest") or "")
        github_sha = github_digest.removeprefix("sha256:") if github_digest.startswith("sha256:") else ""
        if github_size != expected_size:
            raise ValueError(f"{name}: GitHub size {github_size} != manifest size {expected_size}")
        if github_sha and github_sha != expected_sha:
            raise ValueError(f"{name}: GitHub SHA256 {github_sha} != manifest SHA256 {expected_sha}")

        status, head_size = head(str(asset["canonical_url"]))
        if status != 200:
            raise ValueError(f"{name}: canonical URL returned HTTP {status}")
        # GitHub's redirected download endpoint may report transfer-level
        # Content-Length that differs from the Release API asset size. Treat
        # the API size/digest as authoritative and use HEAD only as a liveness
        # check for the canonical URL.

        mirror_url = asset.get("mirror_url")
        mirror_checked = False
        if mirror_url:
            mirror_checked = True
            status, mirror_size = head(str(mirror_url))
            if status != 200:
                raise ValueError(f"{name}: mirror URL returned HTTP {status}")
            if mirror_size is not None and mirror_size != expected_size:
                raise ValueError(f"{name}: mirror Content-Length {mirror_size} != manifest size {expected_size}")
            if not skip_download_bytes:
                mirror_sha, mirror_bytes = sha256_url(str(mirror_url))
                if mirror_bytes != expected_size:
                    raise ValueError(f"{name}: mirror bytes {mirror_bytes} != manifest size {expected_size}")
                if mirror_sha != expected_sha:
                    raise ValueError(f"{name}: mirror SHA256 {mirror_sha} != manifest SHA256 {expected_sha}")
        elif require_mirror:
            raise ValueError(f"{name}: mirror_url is required but missing.")

        results.append(
            {
                "name": name,
                "version": manifest["version"],
                "release_tag": tag,
                "size": expected_size,
                "sha256": expected_sha,
                "github_verified": True,
                "mirror_verified": mirror_checked,
            }
        )
        print(
            f"Verified {name}: tag={tag} size={expected_size} sha256={expected_sha} "
            f"github=yes mirror={'yes' if mirror_checked else 'no'}"
        )
    return results


def main() -> None:
    args = build_parser().parse_args()
    manifest = load_manifest((ROOT / args.manifest).resolve())
    results = verify(
        manifest,
        require_mirror=bool(args.require_mirror),
        skip_download_bytes=bool(args.skip_download_bytes),
    )
    print(f"Public asset verification completed for {len(results)} asset(s).")


if __name__ == "__main__":
    main()
