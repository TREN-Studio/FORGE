from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import time

from forge.config.settings import OperatorSettings


DISALLOWED_TOKENS = {";", "&&", "||", "|", ">", "<", "2>", ">>"}
DISALLOWED_WORDS = {
    "curl",
    "wget",
    "invoke-webrequest",
    "ssh",
    "scp",
    "ftp",
    "pip",
    "npm",
    "pnpm",
    "yarn",
}
ALLOWED_EXECUTABLES = {"python", "py", "pytest", "git", "rg"}
ALLOWED_GIT_SUBCOMMANDS = {"status", "diff", "log", "show", "rev-parse", "branch"}
ALLOWED_PYTHON_MODULES = {"compileall", "pytest", "unittest"}


class GuardedShell:
    """Run a narrow allowlist of local commands inside the workspace."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._cwd = settings.workspace_root.resolve()

    def preview(self, command: str | list[str], timeout_seconds: int | None = None) -> dict:
        argv = self._normalize_command(command)
        self._validate(argv)
        timeout = timeout_seconds or self._settings.shell_timeout_seconds
        return {
            "command": " ".join(argv),
            "argv": argv,
            "cwd": str(self._cwd),
            "timeout_seconds": timeout,
        }

    def run(self, command: str | list[str], timeout_seconds: int | None = None) -> dict:
        preview = self.preview(command, timeout_seconds=timeout_seconds)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                preview["argv"],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=preview["timeout_seconds"],
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Command timed out after {preview['timeout_seconds']}s: {preview['command']}"
            ) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "completed",
            "command": preview["command"],
            "argv": preview["argv"],
            "cwd": preview["cwd"],
            "timeout_seconds": preview["timeout_seconds"],
            "duration_ms": duration_ms,
            "exit_code": completed.returncode,
            "stdout": self._trim(completed.stdout),
            "stderr": self._trim(completed.stderr),
        }

    def _normalize_command(self, command: str | list[str]) -> list[str]:
        if isinstance(command, list):
            argv = [str(item).strip() for item in command if str(item).strip()]
        else:
            argv = shlex.split(command, posix=False)
        if not argv:
            raise ValueError("No shell command was provided.")
        return argv

    def _validate(self, argv: list[str]) -> None:
        command_lower = " ".join(argv).lower()
        if any(token in command_lower for token in DISALLOWED_TOKENS):
            raise PermissionError("Shell metacharacters are blocked.")
        if any(word in command_lower for word in DISALLOWED_WORDS):
            raise PermissionError("Network-capable or package-manager commands are blocked.")

        executable = self._normalize_executable(argv[0])
        if executable not in ALLOWED_EXECUTABLES:
            raise PermissionError(f"Executable `{argv[0]}` is not allowed.")

        if executable in {"python", "py"}:
            self._validate_python(argv)
        elif executable == "git":
            self._validate_git(argv)

        self._validate_path_arguments(argv[1:])

    def _validate_python(self, argv: list[str]) -> None:
        if len(argv) == 2 and argv[1] in {"--version", "-V"}:
            return
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] in ALLOWED_PYTHON_MODULES:
            return
        raise PermissionError(
            "Python execution is limited to `--version` or `-m compileall|pytest|unittest`."
        )

    def _validate_git(self, argv: list[str]) -> None:
        if len(argv) < 2:
            raise PermissionError("Git requires an explicit read-only subcommand.")
        subcommand = argv[1].lower()
        if subcommand not in ALLOWED_GIT_SUBCOMMANDS:
            raise PermissionError(f"Git subcommand `{subcommand}` is not allowed.")

    def _validate_path_arguments(self, argv: list[str]) -> None:
        for arg in argv:
            lowered = arg.lower()
            if lowered.startswith("-"):
                continue
            if any(token in lowered for token in ("http://", "https://", "ssh://")):
                raise PermissionError("Remote targets are blocked.")
            if not self._looks_like_path(arg):
                continue
            candidate = Path(arg)
            if not candidate.is_absolute():
                candidate = (self._cwd / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if self._cwd not in candidate.parents and candidate != self._cwd:
                raise PermissionError(f"Path argument escapes the workspace: {arg}")

    @staticmethod
    def _normalize_executable(raw: str) -> str:
        name = Path(raw.strip()).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        return name

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        return any(token in value for token in ("\\", "/", ".py", ".txt", ".md", ".json", ".toml", ".yml", ".yaml"))

    def _trim(self, text: str) -> str:
        cleaned = text.strip()
        if len(cleaned) > self._settings.shell_max_output_chars:
            return cleaned[: self._settings.shell_max_output_chars].rstrip() + "\n...[truncated]"
        return cleaned
