from __future__ import annotations

from forge.brain.contracts import CompletionState, OperatorResult


class ResponseComposer:
    """Compose the final operator-grade response."""

    def compose(self, result: OperatorResult) -> str:
        validation = result.validation_status.value
        risks = result.risks_or_limitations or ["No critical risks recorded."]

        lines = [
            "1. Objective",
            result.objective,
            "",
            "2. Approach taken",
        ]
        lines.extend(f"- {item}" for item in result.approach_taken)
        lines.extend(
            [
                "",
                "3. Result",
                result.result,
                "",
                "4. Validation status",
                validation,
                "",
                "5. Risks / limitations",
            ]
        )
        lines.extend(f"- {item}" for item in risks)
        lines.extend(
            [
                "",
                "6. Best next action",
                result.best_next_action,
            ]
        )
        if result.mission_trace:
            lines.extend(
                [
                    "",
                    "7. Execution trace",
                ]
            )
            lines.extend(f"- {item}" for item in result.mission_trace[:16])
        if result.mission_id or result.audit_log_path:
            lines.extend(
                [
                    "",
                    "8. Mission audit",
                ]
            )
            if result.mission_id:
                lines.append(f"- Mission ID: {result.mission_id}")
            if result.audit_log_path:
                lines.append(f"- Audit log: {result.audit_log_path}")
            if result.resumed_from_step:
                lines.append(f"- Resumed from: {result.resumed_from_step}")
        if result.agent_reviews:
            lines.extend(
                [
                    "",
                    "9. Agent reviews",
                ]
            )
            for review in result.agent_reviews[:6]:
                confidence = f" ({review.confidence:.2f})" if review.confidence is not None else ""
                lines.append(f"- {review.agent}{confidence}: {' | '.join(review.notes[:2])}")
        return "\n".join(lines)

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
