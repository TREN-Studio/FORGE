from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
from pathlib import Path
from typing import Any

import paramiko


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mirror canonical FORGE release assets to Hostinger without rebuilding them."
    )
    parser.add_argument("--manifest", default="release-assets/release-manifest.json")
    parser.add_argument("--assets-dir", default="release-assets")
    parser.add_argument(
        "--remote-root",
        default=os.getenv("HOSTINGER_REMOTE_ROOT", "domains/trenstudio.com/public_html/FORGE"),
    )
    parser.add_argument("--mirror-dir", default=os.getenv("FORGE_RELEASE_MIRROR_DIR", "downloads"))
    parser.add_argument("--host", default=os.getenv("HOSTINGER_HOST"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HOSTINGER_PORT", "22")))
    parser.add_argument("--username", default=os.getenv("HOSTINGER_USERNAME"))
    parser.add_argument("--password", default=os.getenv("HOSTINGER_PASSWORD"))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def require(value: str | None, label: str) -> str:
    if value:
        return value
    raise ValueError(f"Missing required mirror setting: {label}")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("version") or not manifest.get("release_tag"):
        raise ValueError("Release manifest must include version and release_tag.")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Release manifest must include at least one asset.")
    return manifest


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = ""
    for part in remote_dir.split("/"):
        if not part:
            continue
        current = f"{current}/{part}" if current else part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_remote(sftp: paramiko.SFTPClient, remote_path: str) -> str:
    digest = hashlib.sha256()
    with sftp.open(remote_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def mirror_assets(
    *,
    manifest_path: Path,
    assets_dir: Path,
    remote_root: str,
    mirror_dir: str,
    host: str,
    port: int,
    username: str,
    password: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    manifest = load_manifest(manifest_path)
    remote_root = remote_root.strip("/")
    mirror_root = posixpath.join(remote_root, mirror_dir.strip("/"))
    results: list[dict[str, Any]] = []

    if dry_run:
        for asset in manifest["assets"]:
            asset_path = assets_dir / str(asset["name"])
            results.append(_dry_run_result(asset, asset_path, mirror_root))
        results.append({"name": manifest_path.name, "remote_path": posixpath.join(remote_root, manifest_path.name)})
        return results

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=username, password=password, timeout=30)
    sftp = client.open_sftp()
    try:
        ensure_remote_dir(sftp, mirror_root)
        ensure_remote_dir(sftp, remote_root)

        for asset in manifest["assets"]:
            asset_name = str(asset["name"])
            local_path = assets_dir / asset_name
            if not local_path.exists():
                raise FileNotFoundError(f"Missing local release asset: {local_path}")
            local_size = local_path.stat().st_size
            local_sha = sha256_file(local_path)
            expected_size = int(asset["size"])
            expected_sha = str(asset["sha256"])
            if local_size != expected_size:
                raise ValueError(f"{asset_name}: local size {local_size} != manifest size {expected_size}")
            if local_sha != expected_sha:
                raise ValueError(f"{asset_name}: local SHA256 {local_sha} != manifest SHA256 {expected_sha}")

            remote_path = posixpath.join(mirror_root, asset_name)
            sftp.put(str(local_path), remote_path)
            remote_stat = sftp.stat(remote_path)
            remote_sha = sha256_remote(sftp, remote_path)
            if int(remote_stat.st_size) != local_size:
                raise ValueError(f"{asset_name}: mirror size {remote_stat.st_size} != local size {local_size}")
            if remote_sha != local_sha:
                raise ValueError(f"{asset_name}: mirror SHA256 {remote_sha} != local SHA256 {local_sha}")
            results.append(
                {
                    "name": asset_name,
                    "remote_path": remote_path,
                    "size": local_size,
                    "sha256": local_sha,
                    "verified": True,
                }
            )
            print(f"Mirrored {asset_name} -> {remote_path} ({local_size} bytes, sha256 {local_sha})")

        manifest_remote_path = posixpath.join(remote_root, "release-manifest.json")
        sftp.put(str(manifest_path), manifest_remote_path)
        results.append(
            {
                "name": "release-manifest.json",
                "remote_path": manifest_remote_path,
                "size": manifest_path.stat().st_size,
                "sha256": sha256_file(manifest_path),
                "verified": True,
            }
        )
        print(f"Uploaded release-manifest.json -> {manifest_remote_path}")
    finally:
        sftp.close()
        client.close()
    return results


def _dry_run_result(asset: dict[str, Any], asset_path: Path, mirror_root: str) -> dict[str, Any]:
    asset_name = str(asset["name"])
    return {
        "name": asset_name,
        "local_path": str(asset_path),
        "remote_path": posixpath.join(mirror_root, asset_name),
        "expected_size": asset.get("size"),
        "expected_sha256": asset.get("sha256"),
    }


def main() -> None:
    args = build_parser().parse_args()
    manifest_path = (ROOT / args.manifest).resolve()
    assets_dir = (ROOT / args.assets_dir).resolve()
    results = mirror_assets(
        manifest_path=manifest_path,
        assets_dir=assets_dir,
        remote_root=args.remote_root,
        mirror_dir=args.mirror_dir,
        host=args.host or ("dry-run.local" if args.dry_run else require(args.host, "HOSTINGER_HOST")),
        port=int(args.port),
        username=args.username or ("dry-run" if args.dry_run else require(args.username, "HOSTINGER_USERNAME")),
        password=args.password or ("dry-run" if args.dry_run else require(args.password, "HOSTINGER_PASSWORD")),
        dry_run=bool(args.dry_run),
    )
    total = sum(int(item.get("size") or item.get("expected_size") or 0) for item in results)
    print(f"Mirror sync completed. Checked {len(results)} item(s), {total} byte(s).")


if __name__ == "__main__":
    main()
