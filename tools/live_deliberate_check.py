"""
FORGE Deliberative Planner — LIVE smoke test ($0, free-tier Groq only).

Proves that the NEW `PlanningEngine.deliberate()` path works end-to-end on a
REAL free model: it sends a deliberately regex-unmatchable request, receives
an actual LLM response, parses the JSON plan, and prints the ordered steps.

This is the Step-1 verification the Manager ordered: live proof without any
paid-key decision. Run manually (NOT part of CI):

    python tools/live_deliberate_check.py

Exit code 0 = deliberative planning produced a real, parseable plan from a
live free model. Exit code 1 = unavailable / rate-limited / unparseable
(reported honestly, not masked).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --- load the FREE Groq key (never printed) -----------------------------------
# Order: $GROQ_API_KEY env var, then $FORGE_KEY_FILE (a one-line
# "groq_api_key=..." or raw-key file). No hardcoded machine paths.
import os as _os
from pathlib import Path as _Path

_VAULT_KEY = _Path(_os.environ.get("FORGE_KEY_FILE", ""))
if not _VAULT_KEY.is_absolute():
    _VAULT_KEY = _Path.cwd() / _VAULT_KEY


def _load_groq_key() -> str | None:
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    key_file = _os.environ.get("FORGE_KEY_FILE", "")
    if not key_file:
        return None
    if not _VAULT_KEY.exists():
        return None
    for line in _VAULT_KEY.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.lower().startswith("groq_api_key="):
            return line.split("=", 1)[1].strip()
        if "gsk_" in line and "=" not in line:
            return line.strip()
    return None


def main() -> int:
    key = _load_groq_key()
    if not key:
        print("[SKIP] No GROQ_API_KEY available — cannot run live check.")
        return 1
    os.environ.setdefault("GROQ_API_KEY", key)

    # Import AFTER env is set so provider registration picks the key up.
    from forge.brain.contracts import IntentKind, RiskLevel, TaskIntent
    from forge.brain.planner import PlanningEngine
    from forge.config.settings import OperatorSettings
    from forge.core.session import ForgeSession
    from forge.safety.guard import SafetyDecision
    from forge.skills.contracts import RoutingDecision
    from forge.skills.registry import SkillRegistry

    print("[BOOT] Building real ForgeSession (free providers only)...")
    session = ForgeSession(memory=False)

    online = session._router.status().get("models_online", 0)
    print(f"[BOOT] Router reports {online} models online.")
    if online == 0:
        print("[FAIL] No live models online (keys missing or providers offline).")
        return 1

    # Real skill lookup with real descriptions, straight from the registry.
    registry = SkillRegistry(OperatorSettings())
    registry.refresh()
    all_skills = registry.list()
    skill_lookup = {skill.name: skill for skill in all_skills}
    print(f"[BOOT] {len(skill_lookup)} skills loaded from registry.")

    # A request crafted to defeat every regex heuristic in the planner —
    # no imperative verbs, no file paths, no URLs, no build/install/publish terms —
    # but still clearly an actionable rewrite task the LLM must decompose.
    request = (
        "Our changelog has drifted into a messy diary voice and it needs a proper "
        "rewrite into a clean spec-sheet format. Work out the right way to restructure "
        "and rewrite the existing entries so the tone is consistent."
    )

    intent = TaskIntent(
        raw_request=request,
        objective="Rework the changelog tone and structure",
        primary_intent=IntentKind.TRANSFORMATION,
        task_type="general",
    )
    routing = RoutingDecision(
        mode="sequential",
        selected_skills=["file-reader", "document-editor"],
        fallback_skills=[],
        matches=[],
        reasons=["manual smoke test"],
    )
    safety = SafetyDecision(
        risk_level=RiskLevel.LOW,
        blocked=False,
        requires_confirmation=False,
        use_dry_run=False,
        reasons=["smoke test"],
    )

    # Use REAL skills that exist in the registry so the descriptions are genuine.
    available = [name for name in ("file-reader", "document-editor", "file-editor", "web-reader") if name in skill_lookup]
    if len(available) >= 2:
        routing.selected_skills = available[:3]
    print(f"[BOOT] Routing skills: {routing.selected_skills}")

    print("[CALL] deliberate() -> live free model...")
    planner = PlanningEngine()
    steps = planner.deliberate(
        request=request,
        safety=safety,
        routing=routing,
        session=session,
        max_steps=5,
        skill_lookup=skill_lookup,
    )

    if not steps:
        print("[FAIL] deliberate() returned no plan (model unavailable, "
              "rate-limited, or output unparseable). Honest failure — see logs.")
        return 1

    print(f"[OK] Live model produced a parseable plan with {len(steps)} step(s):")
    for s in steps:
        deps = f" (after {','.join(s.depends_on)})" if s.depends_on else ""
        print(f"  - [{s.skill}] {s.action}{deps}")

    if any(s.skill for s in steps):
        print("[PASS] Deliberative planning works end-to-end on a live free model.")
        return 0
    print("[FAIL] Plan parsed but carried no usable skills.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
