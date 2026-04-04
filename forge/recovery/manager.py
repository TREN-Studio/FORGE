from __future__ import annotations

from dataclasses import dataclass

from forge.brain.contracts import CompletionState


@dataclass(slots=True)
class RecoveryDecision:
    action: str
    reason: str
    fallback_skill: str | None = None


class RecoveryManager:
    """Classify failures and decide retry, fallback, or abort."""

    def __init__(self, max_retries_per_step: int = 2) -> None:
        self._max_retries_per_step = max_retries_per_step

    def for_exception(self, attempt: int, error: Exception, fallback_skill: str | None = None) -> RecoveryDecision:
        message = str(error).lower()
        if isinstance(error, PermissionError):
            return RecoveryDecision(action="abort", reason="Execution blocked by safety policy.")
        if isinstance(error, (ValueError, FileNotFoundError, FileExistsError, IsADirectoryError)):
            return RecoveryDecision(action="abort", reason="Execution request was invalid or incomplete.")
        if attempt < self._max_retries_per_step and any(
            token in message for token in ("timeout", "temporary", "rate", "connection")
        ):
            return RecoveryDecision(action="retry", reason="Transient execution issue detected.")
        if fallback_skill:
            return RecoveryDecision(action="fallback", reason="Switching to fallback skill.", fallback_skill=fallback_skill)
        return RecoveryDecision(action="abort", reason="Non-recoverable execution failure.")

    def for_validation(
        self,
        attempt: int,
        status: CompletionState,
        fallback_skill: str | None = None,
    ) -> RecoveryDecision:
        if status == CompletionState.NEEDS_HUMAN_CONFIRMATION:
            return RecoveryDecision(action="abort", reason="Human confirmation required.")
        if status in {CompletionState.FAILED, CompletionState.NEEDS_RETRY} and attempt < self._max_retries_per_step:
            return RecoveryDecision(action="retry", reason="Validation failure is retryable.")
        if fallback_skill:
            return RecoveryDecision(action="fallback", reason="Validation failed; switching skill.", fallback_skill=fallback_skill)
        return RecoveryDecision(action="abort", reason="Validation did not pass.")
