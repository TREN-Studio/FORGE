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
SYSTEM_TERMS = {
    "system",
    "computer",
    "machine",
    "device",
    "host",
    "operating",
    "os",
    "windows",
    "linux",
    "macos",
    "hardware",
    "cpu",
    "ram",
    "disk",
    "حاسوب",
    "جهاز",
    "نظام",
    "تشغيل",
    "مواصفات",
}
PATH_HINTS = ("/", "\\", ".py", ".ts", ".tsx", ".js", ".json", ".md", ".yml", ".yaml", ".toml", ".html", ".htm")
EDIT_TERMS = {"write", "edit", "modify", "create", "update", "append", "prepend", "replace", "patch", "save", "اكتب", "حرر", "حدث", "بدل", "اضف"}
COMMAND_TERMS = {"run", "execute", "command", "shell", "terminal", "compile", "pytest", "git", "rg", "نفذ", "شغل", "امر", "ترمنال"}
BROWSER_TERMS = {"browse", "visit", "website", "web", "page", "url", "browser", "navigate", "click", "fill", "form", "link", "site", "open", "extract", "افتح", "تصفح", "موقع", "صفحة", "رابط", "اضغط", "املأ", "متصفح", "استخرج"}


READ_TERMS = {"read", "inspect", "analyze", "analyse", "review", "summarize", "summarise", "extract", "identify", "understand"}
NEGATED_EDIT_PHRASES = ("do not edit", "don't edit", "without editing", "no edit", "read-only")


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
        fallback_matches = [match for match in viable if match.skill_name not in selected_names]
        fallback_matches.sort(key=lambda match: self._fallback_priority(intent, match))
        fallbacks = [match.skill_name for match in fallback_matches[: self._settings.max_fallback_skills]]
        mode = "pipeline" if len(selected_names) > 1 else "single_skill"

        reasons: list[str] = []
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
        if self._is_read_only_workspace_request(intent.raw_request):
            preferred = "codebase-analyzer" if self._is_code_or_project_request(intent.raw_request) else "file-reader"
            pipeline = self._specific_pipeline(matches, (preferred,))
            if pipeline:
                return pipeline
            pipeline = self._specific_pipeline(matches, ("workspace-inspector",))
            if pipeline:
                return pipeline

        if self._requires_evidence_before_edit(intent.raw_request):
            preferred = "codebase-analyzer" if self._is_code_or_project_request(intent.raw_request) else "file-reader"
            pipeline = self._specific_pipeline(matches, (preferred, "file-editor"))
            if pipeline:
                return pipeline

        if self._has_term_overlap(intent.raw_request, EDIT_TERMS) and self._has_term_overlap(intent.raw_request, COMMAND_TERMS):
            pipeline = self._specific_pipeline(matches, ("file-editor", "shell-executor"))
            if pipeline:
                return pipeline

        if self._has_term_overlap(intent.raw_request, BROWSER_TERMS) or self._has_url_hint(intent.raw_request):
            pipeline = self._specific_pipeline(matches, ("browser-executor",))
            if pipeline:
                return pipeline

        if matches and matches[0].skill_name.lower() in {"file-editor", "shell-executor", "browser-executor"}:
            return [matches[0]]

        if len(intent.intents) == 1:
            return [matches[0]]

        desired_categories: list[str] = []
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
            used_names.add(matches[0].skill_name)

        target_count = min(max(2, len(intent.intents)), self._settings.max_plan_steps, len(matches))
        for match in matches:
            if len(selected) >= target_count:
                break
            if match.skill_name in used_names:
                continue
            selected.append(match)
            used_names.add(match.skill_name)
        return selected[: self._settings.max_plan_steps]

    def _is_read_only_workspace_request(self, text: str) -> bool:
        lowered = text.lower()
        if not self._has_explicit_path(text) and not self._is_workspace_task(text):
            return False
        if any(phrase in lowered for phrase in NEGATED_EDIT_PHRASES):
            return True
        return self._has_term_overlap(text, READ_TERMS) and not self._has_term_overlap(text, EDIT_TERMS)

    def _requires_evidence_before_edit(self, text: str) -> bool:
        if not self._has_explicit_path(text):
            return False
        if any(phrase in text.lower() for phrase in NEGATED_EDIT_PHRASES):
            return False
        return self._has_term_overlap(text, READ_TERMS) and self._has_term_overlap(text, EDIT_TERMS)

    @staticmethod
    def _is_code_or_project_request(text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ("code", "bug", "fix", "test", "function", "class", "module", "project", "repo", "repository", "codebase"))

    @staticmethod
    def _specific_pipeline(matches: list[SkillMatch], names: tuple[str, ...]) -> list[SkillMatch]:
        selected: list[SkillMatch] = []
        for target in names:
            for match in matches:
                if match.skill_name.lower() == target:
                    selected.append(match)
                    break
        return selected

    def _fallback_priority(self, intent: TaskIntent, match: SkillMatch) -> tuple[int, float, str]:
        lowered = match.skill_name.lower()

        if self._is_system_task(intent.raw_request):
            if "system" in lowered:
                return (0, -match.score, lowered)
            if any(token in lowered for token in ("workspace", "codebase", "file")):
                return (1, -match.score, lowered)
            if "browser" in lowered:
                return (2, -match.score, lowered)
            return (4, -match.score, lowered)

        if self._has_term_overlap(intent.raw_request, BROWSER_TERMS) or self._has_url_hint(intent.raw_request):
            if "browser" in lowered:
                return (0, -match.score, lowered)
            if any(token in lowered for token in ("research", "writer", "artifact")):
                return (4, -match.score, lowered)
            return (2, -match.score, lowered)

        if self._is_workspace_task(intent.raw_request):
            if any(token in lowered for token in ("workspace", "codebase", "file")):
                return (0, -match.score, lowered)
            if "browser" in lowered:
                return (1, -match.score, lowered)
            if any(token in lowered for token in ("research", "affiliate", "writer")):
                return (3, -match.score, lowered)

        return (2, -match.score, lowered)

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
            score += min(0.25, overlap / 8)
            reasons.append("Strong overlap with skill contract.")

        if intent.requested_output.lower() in skill.searchable_text:
            score += 0.10
            reasons.append("Output format fits the requested artifact.")

        if any(intent_name.value in skill.searchable_text for intent_name in intent.intents):
            score += 0.15
            reasons.append("Skill mentions the detected intent directly.")

        if self._is_workspace_task(intent.raw_request) and self._has_term_overlap(skill.searchable_text, WORKSPACE_TERMS):
            score += 0.14
            reasons.append("Workspace-aware skill fits the local project task.")

        if self._has_explicit_path(intent.raw_request) and self._has_term_overlap(skill.searchable_text, {"file", "path", "line", "excerpt", "ملف", "مسار"}):
            score += 0.12
            reasons.append("Skill can operate on an explicit file reference.")
            if "explicit" in skill.searchable_text or "specific file" in skill.searchable_text or "specific files" in skill.searchable_text:
                score += 0.10
                reasons.append("Skill is specialized for explicit file inspection.")
        elif self._has_term_overlap(intent.raw_request, {"repo", "repository", "project", "codebase", "workspace", "مشروع"}):
            if self._has_term_overlap(skill.searchable_text, {"repo", "repository", "project", "codebase", "workspace", "مشروع"}):
                score += 0.06
                reasons.append("Skill is specialized for repo-wide inspection.")

        if self._is_system_task(intent.raw_request):
            system_specialist = self._is_system_specialist(skill)
            if system_specialist:
                score += 0.32
                reasons.append("Skill is specialized for local system inspection.")
            elif self._has_term_overlap(skill.searchable_text, SYSTEM_TERMS):
                score += 0.06
                reasons.append("Skill has partial system-inspection relevance.")
            else:
                score -= 0.16
                reasons.append("General skill is less precise than a host-specific inspection skill.")
            if self._has_term_overlap(skill.searchable_text, WORKSPACE_TERMS):
                score -= 0.12
                reasons.append("Workspace-oriented skill is less precise for a host inspection request.")

        lowered_name = skill.name.lower()
        if "file-editor" in lowered_name and self._has_term_overlap(intent.raw_request, EDIT_TERMS):
            score += 0.30
            reasons.append("Skill is specialized for safe file mutation.")
            if any(phrase in intent.raw_request.lower() for phrase in NEGATED_EDIT_PHRASES):
                score -= 0.45
                reasons.append("Request explicitly asks not to edit files.")
        if "shell-executor" in lowered_name and self._has_term_overlap(intent.raw_request, COMMAND_TERMS):
            score += 0.30
            reasons.append("Skill is specialized for guarded command execution.")
        if "browser-executor" in lowered_name and (
            self._has_term_overlap(intent.raw_request, BROWSER_TERMS) or self._has_url_hint(intent.raw_request)
        ):
            score += 0.34
            reasons.append("Skill is specialized for live browser execution.")
        elif (
            self._has_term_overlap(intent.raw_request, BROWSER_TERMS) or self._has_url_hint(intent.raw_request)
        ) and any(token in lowered_name for token in ("writer", "artifact", "reader")):
            score -= 0.08
            reasons.append("Non-browser skill is less precise for a live web task.")

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
        return SkillRouter._has_term_overlap(text, WORKSPACE_TERMS)

    @staticmethod
    def _is_system_task(text: str) -> bool:
        return SkillRouter._has_term_overlap(text, SYSTEM_TERMS)

    @staticmethod
    def _is_system_specialist(skill: SkillDefinition) -> bool:
        searchable = skill.searchable_text
        name = skill.name.lower()
        specialist_markers = {
            "system-inspector",
            "local machine",
            "operating system",
            "local computer",
            "computer",
            "host",
            "device",
            "hardware",
        }
        return any(marker in name or marker in searchable for marker in specialist_markers)

    @staticmethod
    def _has_explicit_path(text: str) -> bool:
        lowered = text.lower()
        return any(hint in lowered for hint in PATH_HINTS)

    @staticmethod
    def _has_url_hint(text: str) -> bool:
        lowered = text.lower()
        return "http://" in lowered or "https://" in lowered or "file://" in lowered or "data:text/html" in lowered

    @staticmethod
    def _has_term_overlap(text: str, terms: set[str]) -> bool:
        tokens = set(re.findall(r"[\w/-]+", text.lower(), flags=re.UNICODE))
        normalized_terms = {term.lower() for term in terms}
        return bool(tokens.intersection(normalized_terms))
