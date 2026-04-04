from __future__ import annotations

import getpass
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
from typing import Any


try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


def inspect_local_system(workspace_root: Path) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    memory = _memory_snapshot()
    disk = _disk_snapshot(workspace_root)
    network = _network_snapshot()

    facts = {
        "hostname": socket.gethostname(),
        "username": getpass.getuser(),
        "platform": platform.system() or "Unknown",
        "platform_release": platform.release() or "Unknown",
        "platform_version": platform.version() or "Unknown",
        "platform_label": platform.platform() or "Unknown",
        "machine": platform.machine() or "Unknown",
        "architecture": ", ".join(item for item in platform.architecture() if item) or "Unknown",
        "processor": platform.processor() or "Unknown",
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "workspace_root": str(workspace_root),
        "cwd": str(Path.cwd().resolve()),
        "cpu_count_logical": os.cpu_count() or 0,
        "memory": memory,
        "disk": disk,
        "network": network,
    }

    facts["summary"] = (
        f"{facts['platform']} {facts['platform_release']} on {facts['machine']} "
        f"with {facts['cpu_count_logical']} logical CPU(s), "
        f"{memory.get('total_human', 'unknown RAM')}, "
        f"workspace disk free {disk.get('free_human', 'unknown')}."
    )
    return facts


def _memory_snapshot() -> dict[str, Any]:
    if psutil is not None:
        memory = psutil.virtual_memory()
        return {
            "total_bytes": int(memory.total),
            "available_bytes": int(memory.available),
            "used_percent": float(memory.percent),
            "total_human": _human_bytes(int(memory.total)),
            "available_human": _human_bytes(int(memory.available)),
        }

    system = platform.system().lower()
    if system == "windows":
        snapshot = _windows_memory_snapshot()
        if snapshot:
            return snapshot
    if system == "linux":
        snapshot = _linux_memory_snapshot()
        if snapshot:
            return snapshot
    if system == "darwin":
        snapshot = _macos_memory_snapshot()
        if snapshot:
            return snapshot

    return {
        "total_bytes": 0,
        "available_bytes": 0,
        "used_percent": 0.0,
        "total_human": "unknown",
        "available_human": "unknown",
    }


def _windows_memory_snapshot() -> dict[str, Any] | None:
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return {
            "total_bytes": int(status.ullTotalPhys),
            "available_bytes": int(status.ullAvailPhys),
            "used_percent": float(status.dwMemoryLoad),
            "total_human": _human_bytes(int(status.ullTotalPhys)),
            "available_human": _human_bytes(int(status.ullAvailPhys)),
        }
    except Exception:
        return None


def _linux_memory_snapshot() -> dict[str, Any] | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return None

    values: dict[str, int] = {}
    for line in meminfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        number = "".join(ch for ch in raw_value if ch.isdigit())
        if number:
            values[key.strip()] = int(number) * 1024

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used_percent = round(((total - available) / total) * 100, 2) if total else 0.0
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_percent": used_percent,
        "total_human": _human_bytes(total),
        "available_human": _human_bytes(available),
    }


def _macos_memory_snapshot() -> dict[str, Any] | None:
    try:
        total_raw = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        total = int(total_raw)
    except Exception:
        return None

    available = 0
    if psutil is not None:
        available = int(psutil.virtual_memory().available)

    used_percent = round(((total - available) / total) * 100, 2) if total and available else 0.0
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_percent": used_percent,
        "total_human": _human_bytes(total),
        "available_human": _human_bytes(available) if available else "unknown",
    }


def _disk_snapshot(workspace_root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(workspace_root)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    used_percent = round((used / total) * 100, 2) if total else 0.0
    return {
        "path": str(workspace_root.drive or workspace_root.anchor or workspace_root),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_percent": used_percent,
        "total_human": _human_bytes(total),
        "used_human": _human_bytes(used),
        "free_human": _human_bytes(free),
    }


def _network_snapshot() -> dict[str, Any]:
    ip_addresses: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            address = info[4][0]
            if address.startswith("127.") or address == "::1":
                continue
            if address not in ip_addresses:
                ip_addresses.append(address)
    except Exception:
        pass

    return {
        "hostname": socket.gethostname(),
        "ip_addresses": ip_addresses[:8],
    }


def _human_bytes(value: int) -> str:
    if value <= 0:
        return "0 B"

    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024 or unit == "PB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
