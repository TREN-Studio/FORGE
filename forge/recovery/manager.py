from __future__ import annotations

from dataclasses import dataclass

from forge.brain.contracts import CompletionState


@dataclass(slots=True)
class RecoveryDecision:
    action: str
    reason: str
    fallback_skill: str | None = None


class RecoveryManager:
    """Classify failures and decide retry, fallback, or abort.

    Extended (Critic/Recovery expansion, Phase 1): recovery now consumes the
    critic's structured signals — recovery_hint (recover_evidence /
    rebind_context / escape_loop) and the critic's merged status — instead of
    looking only at the raw validation state:

    - recover_evidence: the step's evidence base is broken (hallucinated
      write, unverified output). Retry only makes sense AFTER new grounded
      evidence is gathered; if that is not possible, switch skill.
    - rebind_context: the output drifted from the request. One context-bound
      retry is allowed; a second identical drift aborts with an honest reason.
    - escape_loop: identical dispatch signature repeated across attempts. A
      verbatim retry adds no information — force a fallback skill.
    """

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
        *,
        recovery_hint: str | None = None,
        is_loop: bool = False,
    ) -> RecoveryDecision:
        if status == CompletionState.NEEDS_HUMAN_CONFIRMATION:
            return RecoveryDecision(action="abort", reason="Human confirmation required.")

        # A verbatim retry of an identical dispatch adds no new information:
        # the same executor will produce the same failure. Force a skill
        # switch instead of burning the attempt budget.
        if is_loop and fallback_skill:
            return RecoveryDecision(action="fallback", reason="Identical dispatch repeated — switching skill to escape the loop.", fallback_skill=fallback_skill)
        if is_loop:
            return RecoveryDecision(action="abort", reason="Dispatch loop detected: identical dispatch repeated with no fallback skill available.")

        if recovery_hint == "escape_loop":
            if fallback_skill:
                return RecoveryDecision(action="fallback", reason="Critic detected a dispatch loop; switching skill.", fallback_skill=fallback_skill)
            return RecoveryDecision(action="abort", reason="Critic detected a dispatch loop with no fallback skill available.")

        if recovery_hint == "recover_evidence":
            # Broken evidence: a blind retry would repeat the hallucination.
            # Allow one evidence-gathering retry while budget remains; the
            # orchestrator injects a grounded evidence step first.
            if attempt < self._max_retries_per_step:
                return RecoveryDecision(action="retry", reason="Critic flagged broken evidence; retrying after grounded evidence recovery.")
            if fallback_skill:
                return RecoveryDecision(action="fallback", reason="Evidence stays broken after retries; switching skill.", fallback_skill=fallback_skill)
            return RecoveryDecision(action="abort", reason="Critic flagged broken evidence and no retry budget or fallback remains.")

        if recovery_hint == "rebind_context":
            # Output drifted from the request: allow a context-bound retry,
            # but never more than the configured budget.
            if attempt < self._max_retries_per_step:
                return RecoveryDecision(action="retry", reason="Critic flagged request/output drift; retrying with rebound context.")
            if fallback_skill:
                return RecoveryDecision(action="fallback", reason="Drift persisted after retries; switching skill.", fallback_skill=fallback_skill)
            return RecoveryDecision(action="abort", reason="Critic flagged persistent request/output drift.")

        if status in {CompletionState.FAILED, CompletionState.NEEDS_RETRY} and attempt < self._max_retries_per_step:
            return RecoveryDecision(action="retry", reason="Validation failure is retryable.")
        if fallback_skill:
            return RecoveryDecision(action="fallback", reason="Validation failed; switching skill.", fallback_skill=fallback_skill)
        return RecoveryDecision(action="abort", reason="Validation did not pass.")

    def for_status(
        self,
        attempt: int,
        validation_status: CompletionState,
        critic_status: CompletionState,
        fallback_skill: str | None = None,
        *,
        recovery_hint: str | None = None,
        is_loop: bool = False,
    ) -> RecoveryDecision:
        """Critic-aware wrapper: uses the MERGED status (critic can downgrade
        a FINISHED to PARTIALLY_FINISHED when guards find critical evidence)
        so a critic downgrade now triggers a recovery path instead of being
        silently accepted."""
        order = {
            CompletionState.FINISHED: 0,
            CompletionState.PARTIALLY_FINISHED: 1,
            CompletionState.NEEDS_RETRY: 2,
            CompletionState.NEEDS_HUMAN_CONFIRMATION: 3,
            CompletionState.FAILED: 4,
        }
        merged = critic_status if order[critic_status] > order[validation_status] else validation_status
        # A finished validation the critic downgraded for evidence problems
        # must not be treated as plain PARTIALLY_FINISHED: route it through
        # the hint-aware decision above. WITHOUT a deterministic hint (no
        # hallucination, no drift), a flattering-vs-strict LLM verdict alone
        # must NOT burn a retry or rollback good work — the partial result is
        # accepted honestly instead (LLM verdicts are advisory only).
        if (
            recovery_hint
            and validation_status == CompletionState.FINISHED
            and merged == CompletionState.PARTIALLY_FINISHED
        ):
            return self.for_validation(attempt, CompletionState.NEEDS_RETRY, fallback_skill, recovery_hint=recovery_hint, is_loop=is_loop)
        if merged == CompletionState.FINISHED:
            return RecoveryDecision(action="proceed", reason="Step passed validation and critic review.")
        if validation_status == CompletionState.FINISHED:
            # Validator passed; the LLM critic alone downgraded to partial:
            # accept the honest partial instead of retrying (advisory-only).
            return RecoveryDecision(action="proceed", reason="Validator passed; critic downgrade is advisory without deterministic evidence flags.")
        return self.for_validation(attempt, merged, fallback_skill, recovery_hint=recovery_hint, is_loop=is_loop)
