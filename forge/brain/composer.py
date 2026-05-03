from __future__ import annotations

import re

from forge.brain.contracts import CompletionState, IntentKind, OperatorResult
from forge.brain.identity import enforce_forge_response_guard


class ResponseComposer:
    """Compose the final operator-grade response."""

    def compose(self, result: OperatorResult) -> str:
        action_summary = self._action_completion_summary(result.result)
        if action_summary and result.validation_status == CompletionState.FINISHED:
            return enforce_forge_response_guard(action_summary)

        if not result.step_results and result.intent.primary_intent.value == "conversation":
            return enforce_forge_response_guard(result.result.strip())

        if not result.step_results and result.validation_status == CompletionState.FINISHED:
            return enforce_forge_response_guard(result.result.strip())

        if (
            result.validation_status == CompletionState.FINISHED
            and result.intent.primary_intent in {IntentKind.RESEARCH, IntentKind.ANALYSIS}
            and not result.risks_or_limitations
        ):
            return enforce_forge_response_guard(result.result.strip())

        validation = result.validation_status.value
        risks = result.risks_or_limitations or []
        completed = sum(1 for step in result.step_results if step.status == CompletionState.FINISHED)
        total = len(result.step_results)

        lines = [result.result.strip()]
        if total:
            lines.extend(
                [
                    "",
                    f"Status: {validation}. Steps completed: {completed}/{total}.",
                ]
            )
        else:
            lines.extend(["", f"Status: {validation}."])
        if risks:
            lines.extend(["", "Limitations:"])
            lines.extend(f"- {item}" for item in risks[:4])
        if result.best_next_action:
            lines.extend(["", f"Next: {result.best_next_action}"])
        return enforce_forge_response_guard("\n".join(line for line in lines if line is not None))

    def best_next_action(self, status: CompletionState) -> str:
        if status == CompletionState.FINISHED:
            return "Proceed to the next business action or persist the artifact."
        if status == CompletionState.PARTIALLY_FINISHED:
            return "Review the partial output, then rerun the blocked step with tighter inputs."
        if status == CompletionState.NEEDS_HUMAN_CONFIRMATION:
            return "Review the risk note and provide explicit confirmation before execution."
        if status == CompletionState.NEEDS_RETRY:
            return "Retry the failed step or switch to the suggested fallback skill."
        return "Inspect the failure reason, adjust inputs, and rerun safely."

    @staticmethod
    def _action_completion_summary(text: str) -> str:
        match = re.search(r"Applied\s+(create|update|edit)\s+on\s+`([^`]+)`", str(text or ""), flags=re.IGNORECASE)
        if not match:
            return ""
        operation = match.group(1).lower()
        path = match.group(2).strip()
        verb = "created" if operation == "create" else "updated"
        return f"Done. I {verb} `{path}` and verified the change.\n\nNext: open `{path}` and review the content."
