from __future__ import annotations

import re

from forge.brain.contracts import IntentKind, TaskIntent
from forge.config.settings import OperatorSettings
from forge.skills.contracts import RoutingDecision, SkillDefinition, SkillMatch


INTENT_TO_CATEGORY: dict[IntentKind, tuple[str, ...]] = {
    IntentKind.RESEARCH: ("research",),
    IntentKind.WRITING: ("content", "writing"),
    IntentKind.TRANSFORMATION: ("transformation", "data"),
    IntentKind.PUBLISHING: ("publishing", "operations"),
    IntentKind.AUTOMATION: ("automation", "operations"),
    IntentKind.ORCHESTRATION: ("orchestration", "operations"),
    IntentKind.DEBUGGING: ("debugging", "engineering"),
    IntentKind.ANALYSIS: ("analysis",),
    IntentKind.CONTENT_GENERATION: ("content",),
    IntentKind.STRUCTURED_OUTPUT: ("analysis", "content", "operations"),
}

WORKSPACE_TERMS = {"repo", "repository", "project", "codebase", "workspace", "file", "files", "path", "مشروع", "ملف", "مسار"}
PATH_HINTS = ("/", "\\", ".py", ".ts", ".tsx", ".js", ".json", ".md", ".yml", ".yaml", ".toml")


class SkillRouter:
    """Structured skill routing with trust and safety awareness."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings

    def route(
        self,
        intent: TaskIntent,
        skills: list[SkillDefinition],
    ) -> RoutingDecision:
        if not skills:
            return RoutingDecision(
                mode="reasoning_only",
                selected_skills=[],
                fallback_skills=[],
                matches=[],
                reasons=["No skills are registered."],
            )

        matches = [self._score_skill(intent, skill) for skill in skills]
        matches.sort(key=lambda item: item.score, reverse=True)
        viable = [match for match in matches if match.score >= self._settings.skill_score_threshold]

        if not viable:
            return RoutingDecision(
                mode="reasoning_only",
                selected_skills=[],
                fallback_skills=[],
                matches=matches,
                reasons=["No skill crossed the routing threshold; using reasoning-only path."],
            )

        selected = self._select_pipeline(intent, viable)
        selected_names = [match.skill_name for match in selected]
        fallbacks = [match.skill_name for match in viable if match.skill_name not in selected_names]
        fallbacks = fallbacks[: self._settings.max_fallback_skills]
        mode = "pipeline" if len(selected_names) > 1 else "single_skill"
        reasons = []
        for match in selected:
            reasons.extend(match.reasons)

        return RoutingDecision(
            mode=mode,
            selected_skills=selected_names,
            fallback_skills=fallbacks,
            matches=matches,
            reasons=list(dict.fromkeys(reasons)),
        )

    def _select_pipeline(self, intent: TaskIntent, matches: list[SkillMatch]) -> list[SkillMatch]:
        if len(intent.intents) == 1:
            return [matches[0]]

        desired_categories = []
        for item in intent.intents:
            desired_categories.extend(INTENT_TO_CATEGORY.get(item, ("general",)))

        selected: list[SkillMatch] = []
        used_names: set[str] = set()
        for category in desired_categories:
            for match in matches:
                if match.skill_name in used_names:
                    continue
                if category in " ".join(match.reasons).lower():
                    selected.append(match)
                    used_names.add(match.skill_name)
                    break
        if not selected:
            selected.append(matches[0])
        target_count = min(max(2, len(intent.intents)), self._settings.max_plan_steps, len(matches))
        for match in matches:
            if len(selected) >= target_count:
                break
            if match.skill_name in used_names:
                continue
            selected.append(match)
            used_names.add(match.skill_name)
        return selected[: self._settings.max_plan_steps]

    def _score_skill(self, intent: TaskIntent, skill: SkillDefinition) -> SkillMatch:
        reasons: list[str] = []
        score = 0.0

        primary_categories = set(INTENT_TO_CATEGORY.get(intent.primary_intent, ("general",)))
        all_categories = set()
        for item in intent.intents:
            all_categories.update(INTENT_TO_CATEGORY.get(item, ("general",)))

        if skill.category.lower() in primary_categories:
            score += 0.35
            reasons.append(f"Primary category match: {skill.category}")
        elif skill.category.lower() in all_categories:
            score += 0.18
            reasons.append(f"Supporting category match: {skill.category}")

        overlap = self._keyword_overlap(intent.raw_request, skill.searchable_text)
        if overlap:
            overlap_score = min(0.25, overlap / 8)
            score += overlap_score
            reasons.append("Strong overlap with skill contract.")

        if intent.requested_output.lower() in skill.searchable_text:
            score += 0.10
            reasons.append("Output format fits the requested artifact.")

        if any(intent_name.value in skill.searchable_text for intent_name in intent.intents):
            score += 0.15
            reasons.append("Skill mentions the detected intent directly.")

        if self._is_workspace_task(intent.raw_request) and any(term in skill.searchable_text for term in WORKSPACE_TERMS):
            score += 0.14
            reasons.append("Workspace-aware skill fits the local project task.")

        if self._has_explicit_path(intent.raw_request) and any(term in skill.searchable_text for term in {"file", "path", "line", "excerpt", "ملف", "مسار"}):
            score += 0.12
            reasons.append("Skill can operate on an explicit file reference.")
            if any(term in skill.searchable_text for term in {"explicit", "specific file", "specific files"}):
                score += 0.10
                reasons.append("Skill is specialized for explicit file inspection.")
        elif any(term in intent.raw_request.lower() for term in {"repo", "repository", "project", "codebase", "workspace", "مشروع"}):
            if any(term in skill.searchable_text for term in {"repo", "repository", "project", "codebase", "workspace", "مشروع"}):
                score += 0.06
                reasons.append("Skill is specialized for repo-wide inspection.")

        if skill.trusted:
            score += 0.10
            reasons.append("Skill is trusted for execution.")
        else:
            score -= 0.50
            reasons.append("Skill is untrusted and must not run automatically.")

        if "non-destructive" in skill.searchable_text or "read-only" in skill.searchable_text:
            score += 0.05
            reasons.append("Skill has a low-risk operating profile.")

        return SkillMatch(skill_name=skill.name, score=round(score, 3), reasons=reasons)

    @staticmethod
    def _keyword_overlap(left: str, right: str) -> int:
        left_tokens = set(re.findall(r"[\w/-]+", left.lower(), flags=re.UNICODE))
        right_tokens = set(re.findall(r"[\w/-]+", right.lower(), flags=re.UNICODE))
        return len(left_tokens.intersection(right_tokens))

    @staticmethod
    def _is_workspace_task(text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in WORKSPACE_TERMS)

    @staticmethod
    def _has_explicit_path(text: str) -> bool:
        lowered = text.lower()
        return any(hint in lowered for hint in PATH_HINTS)
