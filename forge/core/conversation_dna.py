"""
FORGE Conversation DNA
=======================
Not just history. The reasoning fingerprint.

When FORGE switches from Groq to Gemini mid-conversation,
this module ensures the new model inherits the same:
  - Thinking style (how FORGE reasoned so far)
  - Active task context (what is being worked on)
  - User preferences detected so far
  - Decisions already made (no re-deciding)

The DNA is injected into every provider call,
regardless of which model actually answers.

Storage: in-memory per session — lives and dies with the session object.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────
#  Markers injected into system prompt
# ─────────────────────────────────────────────

_DNA_HEADER = "## FORGE Conversation Context (DNA)"
_DNA_FOOTER = "## End DNA"

# How many past reasoning steps to remember
_MAX_STEPS = 5
# How many user-detected preferences to keep
_MAX_PREFS = 8
# Max chars for the active task description
_MAX_TASK_CHARS = 300


@dataclass
class _ReasoningStep:
    prompt_summary: str
    approach: str
    outcome: str
    timestamp: float = field(default_factory=time.time)


class ConversationDNA:
    """
    Tracks and injects the evolving reasoning fingerprint of a FORGE session.

    Usage::

        dna = ConversationDNA()

        # Before calling a provider:
        system_block = dna.get_context()  # inject into system prompt

        # After provider responds:
        dna.update(prompt="build a web app", response="I'll start with...")
    """

    def __init__(self) -> None:
        self._steps: list[_ReasoningStep] = []
        self._active_task: str = ""
        self._decisions: list[str] = []          # irrevocable decisions already made
        self._detected_prefs: dict[str, str] = {} # language, style, verbosity...
        self._created_at: float = time.time()
        self._turn_count: int = 0

    # ─────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────

    def get_context(self) -> str:
        """
        Return a compact system-prompt block that anchors the new provider
        to the existing conversation state.  Returns empty string if there
        is nothing meaningful yet.
        """
        if self._turn_count == 0:
            return ""

        lines: list[str] = [_DNA_HEADER, ""]

        if self._active_task:
            lines.append(f"Active task: {self._active_task}")

        if self._decisions:
            lines.append("Decisions already taken (do not revisit):")
            for d in self._decisions[-5:]:
                lines.append(f"  - {d}")

        if self._steps:
            lines.append("Recent reasoning steps:")
            for step in self._steps[-_MAX_STEPS:]:
                lines.append(
                    f"  [{_fmt_time(step.timestamp)}] Prompt: {step.prompt_summary!r} "
                    f"| Approach: {step.approach} | Outcome: {step.outcome}"
                )

        if self._detected_prefs:
            prefs_str = ", ".join(
                f"{k}={v}" for k, v in list(self._detected_prefs.items())[:_MAX_PREFS]
            )
            lines.append(f"User preferences: {prefs_str}")

        lines += ["", _DNA_FOOTER]
        return "\n".join(lines)

    def update(self, *, prompt: str, response: str) -> None:
        """
        Called after every provider response.  Extracts and stores reasoning
        signals from the exchange.
        """
        self._turn_count += 1
        prompt_summary = _summarize(prompt, max_words=12)
        approach = _extract_approach(response)
        outcome = _extract_outcome(response)

        self._steps.append(
            _ReasoningStep(
                prompt_summary=prompt_summary,
                approach=approach,
                outcome=outcome,
            )
        )
        # Keep list bounded
        if len(self._steps) > _MAX_STEPS * 2:
            self._steps = self._steps[-_MAX_STEPS:]

        self._update_active_task(prompt, response)
        self._detect_preferences(prompt, response)

    def record_decision(self, decision: str) -> None:
        """Explicitly record an irrevocable decision (e.g. 'chose Python')."""
        if decision and decision not in self._decisions:
            self._decisions.append(decision)

    def reset(self) -> None:
        """Clear all DNA state (e.g. when the user starts a new conversation)."""
        self._steps.clear()
        self._active_task = ""
        self._decisions.clear()
        self._detected_prefs.clear()
        self._turn_count = 0
        self._created_at = time.time()

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def active_task(self) -> str:
        return self._active_task

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot for debugging."""
        return {
            "turn_count": self._turn_count,
            "active_task": self._active_task,
            "decisions": list(self._decisions),
            "detected_prefs": dict(self._detected_prefs),
            "steps": [
                {
                    "prompt_summary": s.prompt_summary,
                    "approach": s.approach,
                    "outcome": s.outcome,
                }
                for s in self._steps[-_MAX_STEPS:]
            ],
        }

    # ─────────────────────────────────────────
    #  Private helpers
    # ─────────────────────────────────────────

    def _update_active_task(self, prompt: str, response: str) -> None:
        """Infer / update the main task being worked on."""
        task_verbs = (
            "build", "create", "write", "analyze", "analyse", "fix", "debug",
            "research", "design", "implement", "refactor", "optimize", "review",
            "generate", "deploy", "run", "test", "بناء", "إنشاء", "كتابة",
            "تحليل", "تصحيح", "بحث",
        )
        p_lower = prompt.lower()
        for verb in task_verbs:
            if verb in p_lower:
                candidate = _summarize(prompt, max_words=15)
                if candidate:
                    self._active_task = candidate[:_MAX_TASK_CHARS]
                return

    def _detect_preferences(self, prompt: str, _response: str) -> None:
        """Detect user language and style preferences."""
        # Language detection (Arabic vs English)
        arabic_ratio = len(re.findall(r"[\u0600-\u06FF]", prompt)) / max(len(prompt), 1)
        if arabic_ratio > 0.3:
            self._detected_prefs["language"] = "arabic"
        elif arabic_ratio < 0.05:
            self._detected_prefs["language"] = "english"

        # Verbosity preference
        if len(prompt.split()) > 40:
            self._detected_prefs["verbosity"] = "detailed"
        elif len(prompt.split()) < 6:
            self._detected_prefs["verbosity"] = "concise"

        # Code-first preference
        if any(kw in prompt.lower() for kw in ("code", "script", "function", "class", "كود", "سكريبت")):
            self._detected_prefs["mode"] = "code-first"


# ─────────────────────────────────────────────
#  Module-level helpers
# ─────────────────────────────────────────────

def _summarize(text: str, max_words: int = 12) -> str:
    """Return the first max_words words of text, stripped."""
    words = str(text or "").split()
    return " ".join(words[:max_words])


def _extract_approach(response: str) -> str:
    """Extract a short label for what approach the response takes."""
    r = response.lower()
    if any(kw in r for kw in ("step 1", "first,", "firstly", "let's start", "begin by")):
        return "step-by-step"
    if any(kw in r for kw in ("```", "def ", "class ", "import ", "const ", "function")):
        return "code-generation"
    if any(kw in r for kw in ("research", "found that", "according to", "based on")):
        return "research"
    if any(kw in r for kw in ("error", "fix", "bug", "issue", "problem")):
        return "debugging"
    return "reasoning"


def _extract_outcome(response: str) -> str:
    """Extract a short outcome label from the response."""
    r = response.strip()
    if not r:
        return "empty"
    if any(kw in r.lower() for kw in ("done", "completed", "created", "finished", "success")):
        return "completed"
    if any(kw in r.lower() for kw in ("error", "failed", "cannot", "unable")):
        return "failed"
    if "?" in r[-50:]:
        return "clarifying"
    return "in-progress"


def _fmt_time(ts: float) -> str:
    """Format a unix timestamp as HH:MM."""
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%H:%M")
