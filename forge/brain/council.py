from __future__ import annotations

import re
from typing import Any

from forge.brain.contracts import AgentReview, CompletionState, ExecutionPlan, StepExecutionResult


RESEARCH_TERMS = {
    "research",
    "analyze",
    "analysis",
    "summarize",
    "summary",
    "verify",
    "verification",
    "compare",
    "find",
    "inspect",
    "report",
    "brief",
    "researching",
    "تحليل",
    "ابحث",
    "بحث",
    "قارن",
    "تحقق",
    "لخص",
    "تقرير",
}
STOPWORDS = {
    "the",
    "and",
    "then",
    "with",
    "from",
    "into",
    "that",
    "this",
    "your",
    "page",
    "file",
    "save",
    "open",
    "click",
    "fill",
    "extract",
    "run",
}


class ResearchAgent:
    """Prepare browser-heavy missions and convert raw snapshots into verified findings."""

    def prepare_step(self, request: str, step, payload: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        if step.skill == "browser-executor" and self._is_research_request(request):
            notes.append("Research agent marked this browser step as evidence-first.")
        if step.skill == "file-editor" and "content" not in payload:
            notes.append("Research agent expects file content to be grounded in prior evidence.")
        return notes

    def enrich_output(self, request: str, step, output: Any) -> tuple[Any, AgentReview | None]:
        if step.skill != "browser-executor" or not isinstance(output, dict):
            return output, None
        if not self._is_research_request(request):
            return output, None

        enriched = dict(output)
        summary_markdown, confidence, verification = self._summarize_browser_output(request, enriched)
        enriched["research_summary_markdown"] = summary_markdown
        enriched["confidence"] = confidence
        enriched["verification"] = verification

        evidence = list(enriched.get("evidence", []))
        evidence.append(f"confidence:{confidence:.2f}")
        if verification.get("request_overlap_terms"):
            evidence.append(
                "overlap:" + ", ".join(verification["request_overlap_terms"][:6])
            )
        enriched["evidence"] = list(dict.fromkeys(evidence))

        status = CompletionState.FINISHED if verification.get("verified") else CompletionState.PARTIALLY_FINISHED
        review = AgentReview(
            agent="research",
            status=status,
            notes=[
                "Research chain completed: navigate -> snapshot -> summarize -> verify.",
                f"Confidence score: {confidence:.2f}.",
            ],
            confidence=confidence,
        )
        return enriched, review

    @staticmethod
    def _is_research_request(request: str) -> bool:
        lowered = request.lower()
        return any(term in lowered for term in RESEARCH_TERMS)

    def _summarize_browser_output(self, request: str, output: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
        page_state = output.get("page_state") or {}
        headings = [self._entry_text(item) for item in page_state.get("headings", []) if isinstance(item, dict)]
        links = [self._entry_text(item) for item in page_state.get("links", []) if isinstance(item, dict)]
        texts = [self._entry_text(item) for item in page_state.get("text", []) if isinstance(item, dict)]

        request_terms = self._request_terms(request)
        page_terms = self._request_terms(" ".join(headings + texts + links))
        overlap = sorted(request_terms.intersection(page_terms))
        semantic_count = sum(len(value) for value in page_state.values() if isinstance(value, list))

        confidence = 0.35
        if output.get("current_url"):
            confidence += 0.15
        if semantic_count >= 4:
            confidence += 0.2
        elif semantic_count >= 1:
            confidence += 0.1
        if overlap:
            confidence += min(0.2, 0.04 * len(overlap))
        if str(output.get("snapshot_text", "")).strip():
            confidence += 0.1
        confidence = max(0.15, min(0.95, confidence))

        verified = bool(output.get("current_url")) and semantic_count > 0
        lines = [
            "# Browser Research Summary",
            f"- Source URL: {output.get('current_url', '') or 'unknown'}",
            f"- Confidence: {confidence:.2f}",
            f"- Verified: {'yes' if verified else 'no'}",
        ]
        if headings:
            lines.append(f"- Headings: {', '.join(headings[:4])}")
        if texts:
            lines.append(f"- Key text: {', '.join(texts[:4])}")
        if links:
            lines.append(f"- Links: {', '.join(links[:4])}")
        if overlap:
            lines.append(f"- Request overlap: {', '.join(overlap[:8])}")

        verification = {
            "verified": verified,
            "source_url": output.get("current_url", ""),
            "semantic_nodes": semantic_count,
            "request_overlap_terms": overlap[:10],
        }
        return "\n".join(lines), confidence, verification

    @staticmethod
    def _request_terms(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-zA-Z0-9_-]{3,}", text.lower())
            if token not in STOPWORDS
        }

    @staticmethod
    def _entry_text(entry: dict[str, Any]) -> str:
        for key in ("name", "value", "description", "label"):
            value = str(entry.get(key, "")).strip()
            if value:
                return value
        return ""


class ActionAgent:
    """Owns execution dispatch and keeps the mission trace explicit."""

    @staticmethod
    def dispatch_notes(step) -> list[str]:
        tool_name = step.tool or step.skill or "reasoning"
        return [f"Action agent executing `{tool_name}`."]


class CriticAgent:
    """Reviews each step and the final mission before the user sees it."""

    def review_step(self, request: str, step, output: Any, validation_status: CompletionState) -> AgentReview:
        notes: list[str] = []
        confidence: float | None = None
        status = validation_status

        if isinstance(output, dict):
            confidence_value = output.get("confidence")
            if isinstance(confidence_value, (int, float)):
                confidence = float(confidence_value)
                if confidence < 0.55 and status == CompletionState.FINISHED:
                    status = CompletionState.PARTIALLY_FINISHED
                    notes.append("Critic downgraded this result because confidence is too low.")

            verification = output.get("verification")
            if isinstance(verification, dict) and not verification.get("verified", True):
                if status == CompletionState.FINISHED:
                    status = CompletionState.PARTIALLY_FINISHED
                notes.append("Critic could not fully verify the browser-derived output.")

            if step.skill == "file-editor" and output.get("changed") is False:
                if status == CompletionState.FINISHED:
                    status = CompletionState.PARTIALLY_FINISHED
                notes.append("Critic detected that the file edit produced no change.")

            if step.skill == "shell-executor" and output.get("exit_code") == 0 and not output.get("stdout") and not output.get("stderr"):
                notes.append("Critic accepted the shell step but flagged minimal observable output.")

        if not notes:
            notes.append("Critic accepted the step output.")
        return AgentReview(agent="critic", status=status, notes=notes, confidence=confidence)

    def review_mission(self, plan: ExecutionPlan, step_results: list[StepExecutionResult]) -> AgentReview:
        if not step_results:
            return AgentReview(
                agent="critic",
                status=CompletionState.FAILED,
                notes=["Mission produced no step results."],
                confidence=0.0,
            )

        statuses = {step.status for step in step_results}
        if CompletionState.FAILED in statuses:
            return AgentReview(
                agent="critic",
                status=CompletionState.PARTIALLY_FINISHED if CompletionState.FINISHED in statuses else CompletionState.FAILED,
                notes=["Mission ended with at least one failed step."],
                confidence=0.45,
            )
        if CompletionState.PARTIALLY_FINISHED in statuses:
            return AgentReview(
                agent="critic",
                status=CompletionState.PARTIALLY_FINISHED,
                notes=["Mission completed, but one or more steps remain partial."],
                confidence=0.65,
            )
        if len(step_results) < len(plan.steps):
            return AgentReview(
                agent="critic",
                status=CompletionState.PARTIALLY_FINISHED,
                notes=["Mission stopped before every planned step executed."],
                confidence=0.55,
            )
        return AgentReview(
            agent="critic",
            status=CompletionState.FINISHED,
            notes=["Mission passed the final critic review."],
            confidence=0.85,
        )
