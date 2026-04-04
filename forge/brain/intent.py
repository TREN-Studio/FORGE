from __future__ import annotations

import re

from forge.brain.contracts import ExecutionClass, IntentKind, RiskLevel, TaskIntent


INTENT_RULES: dict[IntentKind, tuple[str, ...]] = {
    IntentKind.RESEARCH: (
        "research",
        "investigate",
        "find",
        "search",
        "browse",
        "visit",
        "website",
        "web",
        "page",
        "url",
        "locate",
        "compare",
        "study",
        "summarize",
        "ابحث",
        "بحث",
        "تصفح",
        "افتح",
        "موقع",
        "صفحة",
        "رابط",
        "قارن",
        "لخص",
    ),
    IntentKind.WRITING: (
        "write",
        "create",
        "update",
        "modify",
        "draft",
        "rewrite",
        "edit",
        "patch",
        "compose",
        "document",
        "اكتب",
        "حرر",
        "صيغ",
        "وثق",
    ),
    IntentKind.TRANSFORMATION: ("transform", "convert", "extract", "structure", "clean", "حول", "استخرج", "نظم"),
    IntentKind.PUBLISHING: (
        "publish",
        "post",
        "ship",
        "upload",
        "release",
        "save",
        "export",
        "publishable",
        "انشر",
        "ارفع",
        "احفظ",
        "صدر",
        "تصدير",
    ),
    IntentKind.AUTOMATION: (
        "automate",
        "schedule",
        "sync",
        "watch",
        "monitor",
        "run",
        "execute",
        "command",
        "terminal",
        "shell",
        "compile",
        "test",
        "click",
        "fill",
        "browser",
        "navigate",
        "أتمت",
        "راقب",
        "نفذ",
        "شغل",
        "أمر",
        "ترمنال",
        "اضغط",
        "املأ",
        "متصفح",
    ),
    IntentKind.ORCHESTRATION: (
        "orchestrate",
        "pipeline",
        "workflow",
        "multi-step",
        "coordinate",
        "نسق",
        "خطط",
        "خطوات",
    ),
    IntentKind.DEBUGGING: (
        "debug",
        "fix",
        "error",
        "trace",
        "issue",
        "broken",
        "test",
        "compile",
        "patch",
        "اصلح",
        "خطأ",
        "مشكلة",
    ),
    IntentKind.ANALYSIS: (
        "analyze",
        "score",
        "evaluate",
        "audit",
        "assess",
        "inspect",
        "review",
        "read",
        "open",
        "explain",
        "walkthrough",
        "codebase",
        "repo",
        "repository",
        "project",
        "file",
        "path",
        "حلل",
        "افحص",
        "راجع",
        "اشرح",
        "اقرأ",
        "ملف",
        "مسار",
        "مشروع",
    ),
    IntentKind.CONTENT_GENERATION: ("article", "blog", "outline", "copy", "content", "headline", "مقال", "محتوى"),
    IntentKind.STRUCTURED_OUTPUT: ("json", "table", "schema", "yaml", "structured", "markdown", "جدول", "منظم"),
}

RISK_RULES: dict[RiskLevel, tuple[str, ...]] = {
    RiskLevel.HIGH: ("delete", "drop", "overwrite", "destroy", "shutdown", "transfer", "buy", "pay", "احذف", "امسح", "دمر", "ادفع"),
    RiskLevel.MEDIUM: (
        "publish",
        "deploy",
        "execute",
        "run",
        "command",
        "shell",
        "terminal",
        "write",
        "modify",
        "create",
        "replace",
        "append",
        "patch",
        "credentials",
        "secret",
        "token",
        "account",
        "انشر",
        "نفذ",
        "شغل",
        "اكتب",
        "حدث",
        "بدل",
        "أضف",
        "سر",
        "توكن",
        "حساب",
    ),
}


class IntentResolver:
    """Resolve user intent into a compact execution profile."""

    def resolve(self, request: str, memory_context: str = "") -> TaskIntent:
        intent_text = self._strip_runtime_context(request)
        normalized = intent_text.strip() or request.strip()
        routing_text = self._strip_embedded_code(normalized)
        request_tokens = self._tokens(routing_text)
        matched_intents = self._match_intents(request_tokens)
        if not matched_intents:
            matched_intents = [IntentKind.CONVERSATION]

        primary = self._pick_primary_intent(request_tokens, matched_intents)
        task_type = self._map_task_type(primary, matched_intents)
        risk_level = self._risk_level(request_tokens)
        execution_classes = self._execution_classes(matched_intents, risk_level)
        hidden_intent = self._hidden_intent(matched_intents)
        requested_output = self._requested_output(request_tokens)

        return TaskIntent(
            raw_request=routing_text,
            objective=self._objective(normalized),
            primary_intent=primary,
            intents=matched_intents,
            task_type=task_type,
            hidden_intent=hidden_intent,
            requested_output=requested_output,
            execution_classes=execution_classes,
            risk_level=risk_level,
            notes=self._notes(matched_intents, risk_level),
        )

    def _match_intents(self, tokens: set[str]) -> list[IntentKind]:
        matched: list[IntentKind] = []
        for intent, keywords in INTENT_RULES.items():
            if any(keyword in tokens for keyword in keywords):
                matched.append(intent)
        return matched

    def _pick_primary_intent(self, tokens: set[str], intents: list[IntentKind]) -> IntentKind:
        if IntentKind.DEBUGGING in intents:
            return IntentKind.DEBUGGING
        if IntentKind.ORCHESTRATION in intents or IntentKind.AUTOMATION in intents:
            return IntentKind.ORCHESTRATION if IntentKind.ORCHESTRATION in intents else IntentKind.AUTOMATION
        if IntentKind.RESEARCH in intents and IntentKind.WRITING in intents:
            return IntentKind.RESEARCH
        return intents[0]

    def _map_task_type(self, primary: IntentKind, intents: list[IntentKind]) -> str:
        if primary == IntentKind.DEBUGGING:
            return "code"
        if primary in {IntentKind.RESEARCH, IntentKind.ANALYSIS}:
            return "research"
        if primary in {IntentKind.WRITING, IntentKind.CONTENT_GENERATION, IntentKind.PUBLISHING}:
            return "creative"
        if primary == IntentKind.ORCHESTRATION:
            return "reasoning"
        if IntentKind.STRUCTURED_OUTPUT in intents:
            return "general"
        return "general"

    def _risk_level(self, tokens: set[str]) -> RiskLevel:
        if any(keyword in tokens for keyword in RISK_RULES[RiskLevel.HIGH]):
            return RiskLevel.HIGH
        if any(keyword in tokens for keyword in RISK_RULES[RiskLevel.MEDIUM]):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _execution_classes(self, intents: list[IntentKind], risk_level: RiskLevel) -> list[ExecutionClass]:
        classes = [ExecutionClass.REQUIRES_VALIDATION]
        if len(intents) > 1:
            classes.append(ExecutionClass.MULTI_SKILL_PIPELINE)
        else:
            classes.append(ExecutionClass.SINGLE_SKILL)
        if intents == [IntentKind.CONVERSATION]:
            classes = [ExecutionClass.SIMPLE_REASONING]
        if risk_level != RiskLevel.LOW:
            classes.append(ExecutionClass.RISKY_ACTION)
            classes.append(ExecutionClass.REQUIRES_CONFIRMATION)
        classes.append(ExecutionClass.REQUIRES_FALLBACK)
        return list(dict.fromkeys(classes))

    def _hidden_intent(self, intents: list[IntentKind]) -> str:
        if IntentKind.RESEARCH in intents and IntentKind.CONTENT_GENERATION in intents:
            return "User wants research transformed into publishable output."
        if IntentKind.ANALYSIS in intents and IntentKind.STRUCTURED_OUTPUT in intents:
            return "User likely wants a decision-ready artifact, not raw commentary."
        if IntentKind.ORCHESTRATION in intents:
            return "User wants controlled execution, not just advice."
        return "Use the most direct path that produces a verified result."

    def _requested_output(self, tokens: set[str]) -> str:
        if {"file", "save", "export", "ملف", "احفظ", "صدر", "تصدير"}.intersection(tokens):
            return "file"
        if "json" in tokens:
            return "JSON"
        if "table" in tokens:
            return "table"
        if "markdown" in tokens:
            return "markdown"
        if "article" in tokens:
            return "article"
        return "clean final response"

    def _objective(self, request: str) -> str:
        return request.splitlines()[0].strip()[:240]

    def _notes(self, intents: list[IntentKind], risk_level: RiskLevel) -> list[str]:
        notes = []
        if len(intents) > 1:
            notes.append("Task contains multiple execution intents.")
        if risk_level != RiskLevel.LOW:
            notes.append(f"Risk level is {risk_level.value}.")
        if IntentKind.STRUCTURED_OUTPUT in intents:
            notes.append("Output format should be deterministic and inspectable.")
        return notes

    @staticmethod
    def _tokens(text: str) -> set[str]:
        raw_tokens = re.findall(r"[\w/-]+", text.lower(), flags=re.UNICODE)
        normalized: set[str] = set()
        for token in raw_tokens:
            cleaned = token.strip("_-/")
            if not cleaned:
                continue
            normalized.add(cleaned)

            token_variants = [cleaned]
            if cleaned.startswith(("و", "ف")) and len(cleaned) > 3:
                token_variants.append(cleaned[1:])
            for variant in token_variants:
                if variant.startswith("ال") and len(variant) > 4:
                    normalized.add(variant[2:])
                normalized.add(variant)
        return normalized

    @staticmethod
    def _strip_runtime_context(text: str) -> str:
        marker = "[FORGE runtime context]"
        if marker in text:
            return text.split(marker, 1)[0]
        return text

    @staticmethod
    def _strip_embedded_code(text: str) -> str:
        without_blocks = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        return re.sub(r"`[^`]+`", " ", without_blocks)
