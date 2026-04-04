from __future__ import annotations

import re

from forge.tools.shell import GuardedShell


def execute(payload: dict, context) -> dict:
    shell = GuardedShell(context.settings)
    command = _resolve_command(payload)
    timeout_seconds = int(payload.get("timeout_seconds") or context.settings.shell_timeout_seconds)

    if context.dry_run:
        preview = shell.preview(command, timeout_seconds=timeout_seconds)
        return {
            "status": "dry_run",
            "summary": f"Previewed guarded command `{preview['command']}` in `{preview['cwd']}`.",
            "command": preview["command"],
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "evidence": [
                f"command:{preview['command']}",
                f"cwd:{preview['cwd']}",
                f"timeout:{preview['timeout_seconds']}",
            ],
        }

    result = shell.run(command, timeout_seconds=timeout_seconds)
    summary = (
        f"Executed `{result['command']}` with exit code {result['exit_code']} "
        f"in {result['duration_ms']} ms."
    )
    return {
        "status": "completed",
        "summary": summary,
        "command": result["command"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "duration_ms": result["duration_ms"],
        "evidence": [
            f"command:{result['command']}",
            f"exit_code:{result['exit_code']}",
            f"duration_ms:{result['duration_ms']}",
        ],
    }


def _resolve_command(payload: dict) -> str:
    direct = str(payload.get("command") or "").strip()
    if direct:
        return direct

    request = str(payload.get("request", ""))
    blocks = _code_blocks(request)
    if blocks:
        return _cleanup_command(blocks[0])

    inline = re.findall(r"`([^`]+)`", request)
    if inline:
        return _cleanup_command(inline[0])

    match = re.search(
        r"(?:run|execute|command|shell)\s*[: ]\s*(.+)$",
        request,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return _cleanup_command(match.group(1))

    raise ValueError("shell-executor requires an explicit command.")


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:bash|sh|shell|powershell|pwsh|cmd|ps1|text)?\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)


def _cleanup_command(command: str) -> str:
    return command.strip().strip("`").strip()
