from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parents[1]
GITHUB_API = "https://api.github.com/repos/TREN-Studio/FORGE"
EXPECTED_GOOGLE_BRIDGE_URL = "https://www.trenstudio.com/forge-auth/google-bridge/"
EXPECTED_GOOGLE_CLIENT_ID = "1014783821384-pt514o3kfur9b4vfih6svm9k1ljutbmd.apps.googleusercontent.com"


class TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify FORGE public route ownership after deployment.")
    parser.add_argument("--project-url", default="https://www.trenstudio.com/FORGE/")
    parser.add_argument("--downloads-url", default="https://www.trenstudio.com/FORGE/downloads/")
    parser.add_argument("--manifest-url", default="https://www.trenstudio.com/FORGE/release-manifest.json")
    parser.add_argument("--google-bridge-url", default=EXPECTED_GOOGLE_BRIDGE_URL)
    parser.add_argument("--portal-health-url", default="https://www.trenstudio.com/FORGE/portal/api/index.php/health")
    parser.add_argument(
        "--expected-manifest",
        default="",
        help="Optional local manifest path to compare exactly with the public manifest.",
    )
    parser.add_argument("--verify-github-latest", action="store_true")
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
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "FORGE-route-verifier",
    }
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {github_token}"
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_expected_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def verify_clean_title(html: str, expected: str, route: str) -> None:
    parser = TitleParser()
    parser.feed(html)
    title = parser.title
    if title != expected:
        raise ValueError(f"{route} title {title!r} != {expected!r}")
    encoded_markers = ("%20", "%2F", "%3A", "%D8", "%D9")
    if any(marker in title for marker in encoded_markers):
        raise ValueError(f"{route} title contains encoded text: {title!r}")


def verify_no_stale_public_markers(html: str, route: str) -> None:
    forbidden_markers = [
        "v1.1.4",
        "1.1.4",
        "v1.1.3",
        "1.1.3",
        "v1.1.2",
        "1.1.2",
        "v1.1.0",
        "1.1.0",
        "forge start",
        "forge add-key",
        "FORGE-macOS-Starter",
        "FORGE-Linux-Starter",
        "FORGE-Setup-1.1.4",
        "FORGE-Setup-1.1.3",
        "FORGE-Windows-Portable-1.1.4",
        "FORGE-Source-v1.1.4",
        "SHA256SUMS-1.1.4",
    ]
    for marker in forbidden_markers:
        if marker in html:
            raise ValueError(f"{route} contains stale or invalid public marker: {marker}")


def verify_windows_first_download_markup(html: str, route: str) -> None:
    required = [
        "Download FORGE for Windows",
        "Windows Download (Recommended)",
        "FORGE-Setup-1.1.5.exe",
        "FORGE-Windows-Portable-1.1.5.zip",
        "No login required",
        "Works instantly",
        "Includes demo",
        "Press Run Demo",
        "For Developers",
        "pip install forge-agent==1.1.5",
    ]
    for marker in required:
        if marker not in html:
            raise ValueError(f"{route} is missing Windows-first marker: {marker}")
    first_windows = html.find("Download FORGE for Windows")
    first_pypi = html.find("PyPI")
    first_source = html.find("Source Archive")
    if first_windows == -1:
        raise ValueError(f"{route} must expose Windows download first.")
    if first_pypi != -1 and first_pypi < first_windows:
        raise ValueError(f"{route} shows PyPI before Windows download.")
    if first_source != -1 and first_source < first_windows:
        raise ValueError(f"{route} shows Source before Windows download.")
    old_install_snippets = [
        '<span id="cmd-text">pip install forge-agent</span>',
        "navigator.clipboard.writeText('pip install forge-agent')",
        "<div class=\"comment\"># Install FORGE</div>",
        "<h4>Launch and start forging</h4>",
        "<h4>Run <code style=\"color:var(--ember);font-size:12px\">pip install forge-agent</code></h4>",
    ]
    for snippet in old_install_snippets:
        if snippet in html:
            raise ValueError(f"{route} contains duplicate or stale install markup: {snippet}")


def verify_project_root(html: str) -> None:
    verify_clean_title(html, "FORGE - Free Open Reasoning & Generation Engine", "/FORGE/")
    required_markers = [
        "FORGE v1.1.5",
        "WINDOWS DESKTOP READY",
        "Windows Download",
        "Quick Start",
        "GitHub Release v1.1.5",
        "/FORGE/favicon.svg",
    ]
    for marker in required_markers:
        if marker not in html:
            raise ValueError(f"/FORGE/ is missing original project marker: {marker}")
    verify_no_stale_public_markers(html, "/FORGE/")
    verify_windows_first_download_markup(html, "/FORGE/")


def verify_downloads_page(html: str) -> None:
    verify_clean_title(html, "FORGE - Free Open Reasoning & Generation Engine", "/FORGE/downloads/")
    required_markers = [
        "WINDOWS DESKTOP READY",
        "Download FORGE for Windows",
        "Windows Download (Recommended)",
        "GitHub Release v1.1.5",
        "/FORGE/release-manifest.json",
        "/FORGE/favicon.svg",
    ]
    for marker in required_markers:
        if marker not in html:
            raise ValueError(f"/FORGE/downloads/ is missing downloads marker: {marker}")
    if 'href="portal/?from=download"' in html:
        raise ValueError("Downloads page still contains a relative portal link.")
    verify_no_stale_public_markers(html, "/FORGE/downloads/")
    verify_windows_first_download_markup(html, "/FORGE/downloads/")


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


def verify_google_bridge_page(html: str) -> None:
    verify_clean_title(html, "FORGE Google Bridge", "/forge-auth/google-bridge/")
    required_markers = [
        "TREN Studio Auth Bridge",
        "Continue to FORGE with Google",
        "https://www.trenstudio.com/FORGE/portal/api/index.php/auth/google/bridge-complete",
    ]
    for marker in required_markers:
        if marker not in html:
            raise ValueError(f"/forge-auth/google-bridge/ is missing marker: {marker}")
    forbidden_markers = ["postgeniuspro.com", "Postgenius", "PostGenius", "forge-google-bridge"]
    for marker in forbidden_markers:
        if marker in html:
            raise ValueError(f"/forge-auth/google-bridge/ contains legacy bridge marker: {marker}")


def verify_portal_health_google_bridge(payload: dict[str, Any]) -> None:
    google_oauth = payload.get("google_oauth")
    if not isinstance(google_oauth, dict):
        raise ValueError("Portal health response is missing google_oauth.")
    client_id = str(google_oauth.get("client_id", "")).strip()
    if client_id != EXPECTED_GOOGLE_CLIENT_ID:
        raise ValueError(f"Portal health Google client_id {client_id!r} != {EXPECTED_GOOGLE_CLIENT_ID!r}")
    bridge_url = str(google_oauth.get("bridge_url", "")).strip()
    if "postgeniuspro.com" in bridge_url.lower():
        raise ValueError(f"Portal health still points Google bridge at legacy domain: {bridge_url}")
    mode = str(google_oauth.get("mode", "")).strip()
    if mode == "bridge_id_token" and bridge_url != EXPECTED_GOOGLE_BRIDGE_URL:
        raise ValueError(f"Portal health Google bridge {bridge_url!r} != {EXPECTED_GOOGLE_BRIDGE_URL!r}")


def verify_github_latest(manifest: dict[str, Any]) -> None:
    latest = fetch_json(f"{GITHUB_API}/releases/latest")
    if latest.get("prerelease"):
        raise ValueError(f"GitHub latest release is a prerelease: {latest.get('tag_name')}")
    latest_tag = latest.get("tag_name")
    if latest_tag != manifest.get("release_tag"):
        raise ValueError(f"GitHub latest stable release {latest_tag!r} != manifest tag {manifest.get('release_tag')!r}")


def main() -> None:
    args = build_parser().parse_args()
    project_html, project_headers = fetch_text(args.project_url)
    downloads_html, downloads_headers = fetch_text(args.downloads_url)
    bridge_html, bridge_headers = fetch_text(args.google_bridge_url)
    manifest = fetch_json(args.manifest_url)
    portal_health = fetch_json(args.portal_health_url)
    expected = load_expected_manifest((ROOT / args.expected_manifest).resolve()) if args.expected_manifest else None

    verify_project_root(project_html)
    verify_downloads_page(downloads_html)
    verify_google_bridge_page(bridge_html)
    verify_portal_health_google_bridge(portal_health)
    verify_manifest(manifest, expected)
    if args.verify_github_latest:
        verify_github_latest(manifest)

    print(f"Verified project route: {args.project_url} ({len(project_html)} bytes)")
    print(f"Verified downloads route: {args.downloads_url} ({len(downloads_html)} bytes)")
    print(f"Verified Google bridge route: {args.google_bridge_url} ({len(bridge_html)} bytes)")
    print(
        "Verified public manifest: "
        f"version={manifest['version']} tag={manifest['release_tag']} assets={len(manifest['assets'])}"
    )
    if args.verify_github_latest:
        print(f"Verified GitHub latest stable release: {manifest['release_tag']}")
    print(f"Project cache-control: {project_headers.get('Cache-Control', '<unset>')}")
    print(f"Downloads cache-control: {downloads_headers.get('Cache-Control', '<unset>')}")
    print(f"Google bridge cache-control: {bridge_headers.get('Cache-Control', '<unset>')}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Route verification failed: HTTP {exc.code} for {exc.url}") from exc
