from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify FORGE public route ownership after deployment.")
    parser.add_argument("--project-url", default="https://www.trenstudio.com/FORGE/")
    parser.add_argument("--downloads-url", default="https://www.trenstudio.com/FORGE/downloads/")
    parser.add_argument("--manifest-url", default="https://www.trenstudio.com/FORGE/release-manifest.json")
    parser.add_argument(
        "--expected-manifest",
        default="",
        help="Optional local manifest path to compare exactly with the public manifest.",
    )
    return parser


def fetch_text(url: str) -> tuple[str, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "FORGE-route-verifier",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, "replace"), dict(response.headers.items())


def fetch_json(url: str) -> dict[str, Any]:
    text, _headers = fetch_text(url)
    return json.loads(text)


def load_expected_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def verify_project_root(html: str) -> None:
    required_markers = [
        "Reference Project",
        '<span class="hf">FORGE</span>',
        '<span class="fade">Engine.</span>',
    ]
    for marker in required_markers:
        if marker not in html:
            raise ValueError(f"/FORGE/ is missing original project marker: {marker}")
    forbidden_markers = [
        "OPEN SOURCE · MULTIPLATFORM DESKTOP OPERATOR",
        "Download Bundles",
    ]
    for marker in forbidden_markers:
        if marker in html:
            raise ValueError(f"/FORGE/ appears to be serving the downloads page marker: {marker}")


def verify_downloads_page(html: str) -> None:
    required_markers = [
        "OPEN SOURCE · MULTIPLATFORM DESKTOP OPERATOR",
        "Download Bundles",
        "../release-manifest.json",
    ]
    for marker in required_markers:
        if marker not in html:
            raise ValueError(f"/FORGE/downloads/ is missing downloads marker: {marker}")
    if 'href="portal/?from=download"' in html:
        raise ValueError("Downloads page still contains a relative portal link.")


def verify_manifest(actual: dict[str, Any], expected: dict[str, Any] | None) -> None:
    if actual.get("canonical_host") != "github_release":
        raise ValueError("Public release manifest canonical_host must be github_release.")
    if not actual.get("version") or not actual.get("release_tag"):
        raise ValueError("Public release manifest must include version and release_tag.")
    assets = actual.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Public release manifest must include assets.")
    for asset in assets:
        if not asset.get("canonical_url") or not asset.get("sha256") or not asset.get("size"):
            raise ValueError(f"Manifest asset is incomplete: {asset.get('name')}")

    if expected:
        keys = ("version", "release_tag", "canonical_host")
        for key in keys:
            if actual.get(key) != expected.get(key):
                raise ValueError(f"Public manifest {key} {actual.get(key)!r} != expected {expected.get(key)!r}")
        expected_assets = {asset["name"]: asset for asset in expected.get("assets", [])}
        actual_assets = {asset["name"]: asset for asset in assets}
        if set(actual_assets) != set(expected_assets):
            raise ValueError("Public manifest asset names do not match the expected manifest.")
        for name, expected_asset in expected_assets.items():
            actual_asset = actual_assets[name]
            for key in ("size", "sha256", "canonical_url"):
                if actual_asset.get(key) != expected_asset.get(key):
                    raise ValueError(f"{name}: public manifest {key} does not match expected manifest.")


def main() -> None:
    args = build_parser().parse_args()
    project_html, project_headers = fetch_text(args.project_url)
    downloads_html, downloads_headers = fetch_text(args.downloads_url)
    manifest = fetch_json(args.manifest_url)
    expected = load_expected_manifest((ROOT / args.expected_manifest).resolve()) if args.expected_manifest else None

    verify_project_root(project_html)
    verify_downloads_page(downloads_html)
    verify_manifest(manifest, expected)

    print(f"Verified project route: {args.project_url} ({len(project_html)} bytes)")
    print(f"Verified downloads route: {args.downloads_url} ({len(downloads_html)} bytes)")
    print(
        "Verified public manifest: "
        f"version={manifest['version']} tag={manifest['release_tag']} assets={len(manifest['assets'])}"
    )
    print(f"Project cache-control: {project_headers.get('Cache-Control', '<unset>')}")
    print(f"Downloads cache-control: {downloads_headers.get('Cache-Control', '<unset>')}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Route verification failed: HTTP {exc.code} for {exc.url}") from exc
