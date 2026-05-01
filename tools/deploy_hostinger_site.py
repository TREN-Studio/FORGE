from __future__ import annotations

import argparse
import hashlib
import os
import posixpath
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import paramiko


@dataclass(slots=True)
class DeployConfig:
    host: str
    port: int
    username: str
    password: str
    local_root: Path
    remote_root: str
    portal_backend_local_root: Path | None = None
    portal_backend_remote_root: str | None = None
    backup_index: bool = True
    allow_root_index_deploy: bool = False
    dry_run: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy the FORGE static site bundle to Hostinger over SFTP.")
    parser.add_argument("--local-root", default=os.getenv("FORGE_SITE_LOCAL_ROOT", "site"))
    parser.add_argument(
        "--remote-root",
        default=os.getenv("HOSTINGER_REMOTE_ROOT", "domains/trenstudio.com/public_html/FORGE"),
    )
    parser.add_argument(
        "--portal-backend-local-root",
        default=os.getenv("FORGE_SITE_BACKEND_LOCAL_ROOT", "site_backend/forge_portal"),
    )
    parser.add_argument("--portal-backend-remote-root", default=os.getenv("HOSTINGER_PORTAL_BACKEND_ROOT"))
    parser.add_argument("--host", default=os.getenv("HOSTINGER_HOST"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HOSTINGER_PORT", "22")))
    parser.add_argument("--username", default=os.getenv("HOSTINGER_USERNAME"))
    parser.add_argument("--password", default=os.getenv("HOSTINGER_PASSWORD"))
    parser.add_argument("--no-backup-index", action="store_true")
    parser.add_argument(
        "--allow-root-index-deploy",
        action="store_true",
        default=os.getenv("FORGE_ALLOW_ROOT_INDEX_DEPLOY", "").strip().lower() in {"1", "true", "yes"},
        help="Explicitly allow this deployment to own /FORGE/index.html.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def require(value: str | None, label: str) -> str:
    if value:
        return value
    raise ValueError(f"Missing required deployment setting: {label}")


def load_config(args: argparse.Namespace) -> DeployConfig:
    local_root = Path(args.local_root).resolve()
    if not local_root.exists():
        raise FileNotFoundError(f"Local site root does not exist: {local_root}")
    backend_local_root = Path(args.portal_backend_local_root).resolve()
    if not backend_local_root.exists():
        backend_local_root = None
    remote_root = args.remote_root.strip("/")
    backend_remote_root = (
        (args.portal_backend_remote_root or _default_portal_backend_remote_root(remote_root)).strip("/")
        if backend_local_root is not None
        else None
    )
    if args.dry_run:
        return DeployConfig(
            host=args.host or "dry-run.local",
            port=int(args.port),
            username=args.username or "dry-run",
            password=args.password or "dry-run",
            local_root=local_root,
            remote_root=remote_root,
            portal_backend_local_root=backend_local_root,
            portal_backend_remote_root=backend_remote_root,
            backup_index=not args.no_backup_index,
            allow_root_index_deploy=bool(args.allow_root_index_deploy),
            dry_run=True,
        )
    return DeployConfig(
        host=require(args.host, "HOSTINGER_HOST"),
        port=int(args.port),
        username=require(args.username, "HOSTINGER_USERNAME"),
        password=require(args.password, "HOSTINGER_PASSWORD"),
        local_root=local_root,
        remote_root=remote_root,
        portal_backend_local_root=backend_local_root,
        portal_backend_remote_root=backend_remote_root,
        backup_index=not args.no_backup_index,
        allow_root_index_deploy=bool(args.allow_root_index_deploy),
        dry_run=bool(args.dry_run),
    )


def iter_site_files(local_root: Path) -> list[Path]:
    return sorted(
        path
        for path in local_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
        and path.name != "release-manifest.json"
    )


def _default_portal_backend_remote_root(remote_root: str) -> str:
    marker = "/public_html/FORGE"
    if marker in remote_root:
        return remote_root.replace(marker, "/private/forge_portal")
    return posixpath.join(posixpath.dirname(remote_root), "forge_portal_backend")


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


def maybe_backup_index(sftp: paramiko.SFTPClient, remote_root: str) -> str | None:
    remote_index = posixpath.join(remote_root, "index.html")
    try:
        sftp.stat(remote_index)
    except FileNotFoundError:
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_path = posixpath.join(remote_root, f"index.html.bak-{timestamp}")
    sftp.rename(remote_index, backup_path)
    return backup_path


def sha256_remote(sftp: paramiko.SFTPClient, remote_path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        handle = sftp.open(remote_path, "rb")
    except FileNotFoundError:
        return None
    with handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_root_index_policy(config: DeployConfig) -> None:
    if (config.local_root / "index.html").exists() and not config.allow_root_index_deploy:
        raise ValueError(
            "Refusing to deploy site/index.html to /FORGE/index.html. "
            "The /FORGE/ root page is owned by the TREN Studio project page. "
            "Move release/download work under site/downloads/ or pass "
            "--allow-root-index-deploy for an explicit root-page deployment."
        )


def deploy(config: DeployConfig) -> list[tuple[str, int]]:
    uploaded: list[tuple[str, int]] = []
    validate_root_index_policy(config)
    if config.dry_run:
        for local_path in iter_site_files(config.local_root):
            relative = local_path.relative_to(config.local_root).as_posix()
            uploaded.append((posixpath.join(config.remote_root, relative), local_path.stat().st_size))
        if config.portal_backend_local_root and config.portal_backend_remote_root:
            for local_path in iter_site_files(config.portal_backend_local_root):
                relative = local_path.relative_to(config.portal_backend_local_root).as_posix()
                uploaded.append((posixpath.join(config.portal_backend_remote_root, relative), local_path.stat().st_size))
        return uploaded

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        config.host,
        port=config.port,
        username=config.username,
        password=config.password,
        timeout=30,
    )
    sftp = client.open_sftp()
    try:
        ensure_remote_dir(sftp, config.remote_root)
        deploys_root_index = (config.local_root / "index.html").exists()
        remote_index = posixpath.join(config.remote_root, "index.html")
        root_index_before = sha256_remote(sftp, remote_index) if not config.allow_root_index_deploy else None
        if config.backup_index and deploys_root_index:
            backup_path = maybe_backup_index(sftp, config.remote_root)
            if backup_path:
                print(f"Backed up existing index.html -> {backup_path}")
        elif config.backup_index:
            print("Preserving remote index.html; this bundle does not own the /FORGE/ root page.")

        for local_path in iter_site_files(config.local_root):
            relative = local_path.relative_to(config.local_root).as_posix()
            remote_path = posixpath.join(config.remote_root, relative)
            ensure_remote_dir(sftp, posixpath.dirname(remote_path))
            sftp.put(str(local_path), remote_path)
            size = sftp.stat(remote_path).st_size
            uploaded.append((remote_path, size))
            print(f"Uploaded {relative} -> {remote_path} ({size} bytes)")

        if config.portal_backend_local_root and config.portal_backend_remote_root:
            ensure_remote_dir(sftp, config.portal_backend_remote_root)
            for local_path in iter_site_files(config.portal_backend_local_root):
                relative = local_path.relative_to(config.portal_backend_local_root).as_posix()
                remote_path = posixpath.join(config.portal_backend_remote_root, relative)
                ensure_remote_dir(sftp, posixpath.dirname(remote_path))
                sftp.put(str(local_path), remote_path)
                size = sftp.stat(remote_path).st_size
                uploaded.append((remote_path, size))
                print(f"Uploaded backend {relative} -> {remote_path} ({size} bytes)")
        if root_index_before is not None:
            root_index_after = sha256_remote(sftp, remote_index)
            if root_index_after != root_index_before:
                raise RuntimeError(
                    "/FORGE/index.html changed during a downloads/portal deploy. "
                    "Root page deployments require --allow-root-index-deploy."
                )
            print("Verified /FORGE/index.html unchanged.")
    finally:
        sftp.close()
        client.close()
    return uploaded


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args)
    uploaded = deploy(config)
    total_bytes = sum(size for _, size in uploaded)
    print(f"Deployment completed. Uploaded {len(uploaded)} file(s), {total_bytes} byte(s).")


if __name__ == "__main__":
    main()
