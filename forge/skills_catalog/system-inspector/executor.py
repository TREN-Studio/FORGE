from __future__ import annotations

from forge.tools.system import inspect_local_system


def execute(payload: dict, context) -> dict:
    facts = inspect_local_system(context.settings.workspace_root)
    evidence = _build_evidence(facts)
    analysis = _build_analysis_markdown(payload, facts, evidence)

    summary = (
        f"Inspected {facts['hostname']} running {facts['platform']} {facts['platform_release']} "
        f"with {facts['cpu_count_logical']} logical CPU(s), {facts['memory']['total_human']} RAM, "
        f"and {facts['disk']['free_human']} free on the workspace disk."
    )
    return {
        "status": "completed",
        "summary": summary,
        "facts": facts,
        "analysis_markdown": analysis,
        "evidence": evidence,
    }


def _build_evidence(facts: dict) -> list[str]:
    network = facts.get("network", {})
    ip_addresses = network.get("ip_addresses", [])
    evidence = [
        f"platform:{facts['platform']} {facts['platform_release']}",
        f"platform_label:{facts['platform_label']}",
        f"hostname:{facts['hostname']}",
        f"username:{facts['username']}",
        f"machine:{facts['machine']}",
        f"architecture:{facts['architecture']}",
        f"python:{facts['python_version']} | {facts['python_executable']}",
        f"cpu.logical:{facts['cpu_count_logical']}",
        f"memory.total:{facts['memory']['total_human']}",
        f"memory.available:{facts['memory']['available_human']}",
        f"disk.free:{facts['disk']['free_human']} on {facts['disk']['path']}",
        f"workspace:{facts['workspace_root']}",
        f"cwd:{facts['cwd']}",
    ]
    if ip_addresses:
        evidence.append(f"network.ips:{', '.join(ip_addresses)}")
    return evidence


def _build_analysis_markdown(payload: dict, facts: dict, evidence: list[str]) -> str:
    network = facts.get("network", {})
    ip_addresses = network.get("ip_addresses", [])

    lines = [
        "# Objective",
        payload["objective"],
        "",
        "# System Summary",
        f"- Hostname: `{facts['hostname']}`",
        f"- User: `{facts['username']}`",
        f"- OS: `{facts['platform']} {facts['platform_release']}`",
        f"- Platform label: `{facts['platform_label']}`",
        f"- Machine: `{facts['machine']}`",
        f"- Architecture: `{facts['architecture']}`",
        f"- Processor: `{facts['processor']}`",
        f"- Logical CPU count: `{facts['cpu_count_logical']}`",
        f"- RAM total: `{facts['memory']['total_human']}`",
        f"- RAM available: `{facts['memory']['available_human']}`",
        f"- Workspace disk free: `{facts['disk']['free_human']}` on `{facts['disk']['path']}`",
        f"- Python: `{facts['python_version']}`",
        f"- Workspace root: `{facts['workspace_root']}`",
        f"- Current working directory: `{facts['cwd']}`",
    ]

    if ip_addresses:
        lines.extend(
            [
                "",
                "# Network",
                f"- Non-loopback IPs: `{', '.join(ip_addresses)}`",
            ]
        )

    lines.extend(
        [
            "",
            "# Validation",
            "- Report is grounded in local runtime system calls and safe read-only probes.",
            "- No destructive actions were performed.",
            "",
            "# Evidence",
            *[f"- {item}" for item in evidence],
        ]
    )
    return "\n".join(lines)
