from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge import __version__
from forge.brain.contracts import OperatorResult
from forge.brain.operator import ForgeOperator
from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession


@dataclass(slots=True)
class DesktopBootStatus:
    providers: int
    models_online: int
    summary: str


def boot_status() -> DesktopBootStatus:
    session = ForgeSession(memory=False)
    status = session._router.status()
    providers = status.get("providers", 0)
    models_online = status.get("models_online", 0)
    summary = (
        f"FORGE v{__version__} booted with {providers} provider(s) "
        f"and {models_online} live model(s)."
    )
    return DesktopBootStatus(
        providers=providers,
        models_online=models_online,
        summary=summary,
    )


def run_prompt(prompt: str, use_operator: bool = False) -> str:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt is empty.")

    if use_operator:
        return operate_prompt(prompt)["answer"]

    session = ForgeSession(memory=False)
    return session.ask(prompt, task_type="general", remember=False)


def operate_prompt(
    prompt: str,
    confirmed: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt is empty.")

    operator = ForgeOperator(settings=OperatorSettings(enable_memory=False))
    result = operator.handle(prompt, confirmed=confirmed, dry_run=dry_run)
    return _serialize_operator_result(result, operator)


def _serialize_operator_result(result: OperatorResult, operator: ForgeOperator) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload["answer"] = operator.composer.compose(result)
    payload["completed_steps"] = sum(1 for step in result.step_results if step.status.value == "finished")
    payload["total_steps"] = len(result.step_results)
    payload["evidence_count"] = sum(len(step.evidence) for step in result.step_results)
    payload["artifacts_count"] = len(result.artifacts)
    return payload
