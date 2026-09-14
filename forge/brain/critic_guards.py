from __future__ import annotations

"""Deterministic Critic Guards — the fact-checking backbone of the Critic/Recovery loop.

These guards are deliberately rule-based (zero LLM calls): they run before and
after every step, produce machine-checkable evidence (flags with severities and
numeric drift), and give the RecoveryManager concrete signals to act on:

- preflight_step():   pre-execution audit for file-editor steps — catches a
                      missing-content write BEFORE the executor raises, so the
                      orchestrator can insert a grounded evidence step first.
- hallucination_flags(): post-execution audit of file-editor output — detects
                      invented/edited path drift, unverified writes, and
                      no-change writes reported as success.
- drift_score():      lexical drift between the user request and a step
                      output — a cheap numeric "did we answer what was asked".
- loop_signature():   stable signature of (skill, action, target) used by the
                      orchestrator to detect retry loops before they burn
                      the retry budget on identical dispatches.

Every guard returns plain data (dataclasses) so the whole layer serializes
into worker payloads and mission audit logs without custom encoders.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from forge.brain.contracts import CompletionState


@dataclass(slots=True)
class PreFlightDecision:
    """Outcome of the pre-execution audit for one step dispatch."""

    allowed: bool
    reason: str = ""
    missing_content: bool = False
    source_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GuardFlag:
    """One deterministic criticism finding, with a severity scale."""

    code: str
    severity: str  # "info" | "warn" | "critical"
    note: str


@dataclass(slots=True)
class HallucinationAudit:
    """Post-execution hallucination audit for file-editor steps."""

    clean: bool
    flags: list[GuardFlag] = field(default_factory=list)
    target_drift: bool = False
    path_requested: str = ""
    path_delivered: str = ""

    @property
    def has_critical(self) -> bool:
        return any(flag.severity == "critical" for flag in self.flags)


@dataclass(slots=True)
class LoopAudit:
    """Loop-detection audit across attempts of a single step."""

    is_loop: bool
    signature: str
    attempts_seen: int
    distinct_payloads: int


STOPWORDS = {
    "the", "and", "then", "with", "from", "into", "that", "this", "your",
    "page", "file", "save", "open", "click", "fill", "extract", "run",
    "a", "an", "to", "of", "in", "on", "for", "is", "are", "be", "it",
    "as", "at", "by", "or", "if", "not", "no", "do", "does", "did",
}

_PATH_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".toml",
    ".yaml", ".yml", ".sql", ".csv", ".html", ".css",
)


def preflight_step(step, payload: dict[str, Any]) -> PreFlightDecision:
    """Audit a step dispatch BEFORE execution.

    Currently audits file-editor steps for missing content: a write with no
    content is a guaranteed ValueError from the executor. Instead of letting
    it crash, the orchestrator can insert a grounded file-reader step first.
    Returns allowed=True for every step that passes the audit.
    """
    if step.skill != "file-editor":
        return PreFlightDecision(allowed=True)

    has_content = bool(str(payload.get("content") or "").strip())
    has_replace = isinstance(payload.get("find_text"), str) and isinstance(payload.get("replace_text"), str) and bool(
        str(payload.get("find_text") or "").strip()
    )
    if has_content or has_replace:
        return PreFlightDecision(allowed=True)

    # Missing content on a file-editor dispatch: block BEFORE execution and
    # surface every readable path mentioned in the request as evidence source.
    request_paths = extract_request_paths(str(payload.get("request") or ""))
    return PreFlightDecision(
        allowed=False,
        reason="file-editor dispatched without content or find/replace pair.",
        missing_content=True,
        source_paths=request_paths,
    )


def hallucination_audit(request: str, step, output: Any, validation_status: CompletionState) -> HallucinationAudit:
    """Audit file-editor output for hallucinated/unverified writes.

    Deterministic checks against the real workspace facts embedded in the
    executor output: edited path vs the path actually asked for, the
    verified flag from the disk, and changed=False writes.
    """
    flags: list[GuardFlag] = []
    path_requested = ""
    path_delivered = ""
    target_drift = False

    if step.skill == "file-editor" and isinstance(output, dict):
        path_requested = str(step.input_spec.get("target_path") or payload_fallback_target(step) or "").replace("\\", "/")
        path_delivered = str(output.get("edited_path") or "").replace("\\", "/")
        if path_requested and path_delivered and _normalize_path(path_requested) != _normalize_path(path_delivered):
            target_drift = True
            flags.append(
                GuardFlag(
                    code="edited_path_drift",
                    severity="critical",
                    note=f"Critic detected path drift: requested `{path_requested}` but executor wrote `{path_delivered}`.",
                )
            )
        if output.get("verified") is False:
            flags.append(
                GuardFlag(
                    code="unverified_write",
                    severity="critical",
                    note="File-editor reported the write as unverified against the workspace disk.",
                )
            )
        if output.get("changed") is False and validation_status == CompletionState.FINISHED:
            flags.append(
                GuardFlag(
                    code="no_change_write",
                    severity="warn",
                    note="File-editor produced no textual change while the step was reported finished.",
                )
            )

    return HallucinationAudit(
        clean=not flags,
        flags=flags,
        target_drift=target_drift,
        path_requested=path_requested,
        path_delivered=path_delivered,
    )


def payload_fallback_target(step) -> str:
    """Best-effort target path for steps whose input_spec omitted it."""
    return str(step.input_spec.get("target_path") or "")


def drift_score(request: str, output: Any) -> float:
    """Numeric lexical drift between the user request and a step output.

    Returns a value in [0.0, 1.0]: 0.0 = output vocabulary covers the request
    fully, 1.0 = zero overlap (output is unrelated to what was asked). Uses
    the same lexical grounding as ResearchAgent's overlap heuristics, so
    numbers are comparable across the council.
    """
    request_terms = _terms(request)
    if not request_terms:
        return 0.0
    output_text = _output_text(output)
    output_terms = _terms(output_text)
    if not output_terms:
        return 1.0
    overlap = request_terms & output_terms
    return round(1.0 - (len(overlap) / len(request_terms)), 3)


def loop_signature(skill: str | None, action: str, target: str) -> str:
    """Stable signature of a dispatch, used for loop detection."""
    return f"{skill or 'reasoning'}|{action.strip().lower()}|{_normalize_path(target)}"


def loop_audit(signatures: list[str]) -> LoopAudit:
    """Detect whether retries of one step are re-dispatching the identical job.

    A true loop re-sends the same (skill, action, target) with no change: if
    the executor already failed on that exact dispatch, retrying it verbatim
    burns the budget without new information. Recovery should switch skill
    (fallback) instead of retrying.
    """
    if not signatures:
        return LoopAudit(is_loop=False, signature="", attempts_seen=0, distinct_payloads=0)
    signature = signatures[-1]
    attempts_seen = len(signatures)
    distinct_payloads = len(set(signatures))
    is_loop = attempts_seen >= 2 and distinct_payloads == 1
    return LoopAudit(
        is_loop=is_loop,
        signature=signature,
        attempts_seen=attempts_seen,
        distinct_payloads=distinct_payloads,
    )


def audit_summary_to_dict(audit: HallucinationAudit) -> dict[str, Any]:
    """Serialize a hallucination audit into the mission audit log JSON."""
    return {
        "clean": audit.clean,
        "target_drift": audit.target_drift,
        "path_requested": audit.path_requested,
        "path_delivered": audit.path_delivered,
    }


def flags_to_dicts(flags: list[GuardFlag]) -> list[dict[str, Any]]:
    """Serialize guard flags for worker payloads and telemetry."""
    return [{"code": flag.code, "severity": flag.severity, "note": flag.note} for flag in flags]


def extract_request_paths(request: str) -> list[str]:
    """Extract plausible workspace file paths from free text (shared helper)."""
    paths: list[str] = []
    for token in re.findall(r"[\w./\\:-]+", request, flags=re.UNICODE):
        cleaned = token.strip("`'\" ,:;()[]{}<>")
        while cleaned.endswith((".", ",", ";", ":", "!", "?")) and len(cleaned) > 1:
            cleaned = cleaned[:-1]
        lowered = cleaned.lower()
        if len(cleaned) < 3 or cleaned.startswith(("http://", "https://")):
            continue
        if any(lowered.endswith(suffix) for suffix in _PATH_SUFFIXES):
            paths.append(cleaned.replace("\\", "/"))
    return list(dict.fromkeys(paths))


def _output_text(output: Any) -> str:
    if isinstance(output, dict):
        parts: list[str] = []
        for key in ("summary", "research_summary_markdown", "file_excerpt_markdown", "stdout", "content", "snapshot_text"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        if parts:
            return "\n".join(parts)
        try:
            import json

            return json.dumps(output, ensure_ascii=False, default=str)
        except Exception:
            return str(output)
    return str(output)


def _terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_-]{3,}", str(text).lower())
        if token not in STOPWORDS
    }


def _normalize_path(path: str) -> str:
    return str(path).replace("\\", "/").strip().strip("/").lower()
