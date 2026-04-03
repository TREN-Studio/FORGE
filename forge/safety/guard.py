from __future__ import annotations

import re
from dataclasses import dataclass, field

from forge.brain.contracts import RiskLevel, TaskIntent
from forge.config.settings import OperatorSettings
from forge.skills.contracts import RoutingDecision, SkillDefinition


HIGH_RISK_TERMS = {"delete", "drop", "overwrite", "destroy", "wipe", "shutdown", "transfer", "buy", "pay", "احذف", "امسح", "دمر", "ادفع"}
MEDIUM_RISK_TERMS = {"deploy", "publish", "execute", "credential", "secret", "token", "account", "انشر", "نفذ", "توكن", "سر", "حساب"}


@dataclass(slots=True)
class SafetyDecision:
    risk_level: RiskLevel
    blocked: bool
    requires_confirmation: bool
    use_dry_run: bool
    reasons: list[str] = field(default_factory=list)


class SafetyGuard:
    """Evaluate risk before execution."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings

    def evaluate(
        self,
        request: str,
        intent: TaskIntent,
        routing: RoutingDecision,
        skill_lookup: dict[str, SkillDefinition],
        confirmed: bool = False,
        dry_run_requested: bool = False,
    ) -> SafetyDecision:
        tokens = self._tokens(request)
        risk_level = intent.risk_level
        reasons: list[str] = []

        if any(term in tokens for term in HIGH_RISK_TERMS):
            risk_level = RiskLevel.HIGH
            reasons.append("Request contains high-risk terms.")
        elif any(term in tokens for term in MEDIUM_RISK_TERMS) and risk_level == RiskLevel.LOW:
            risk_level = RiskLevel.MEDIUM
            reasons.append("Request contains medium-risk terms.")

        untrusted = [name for name in routing.selected_skills if name in skill_lookup and not skill_lookup[name].trusted]
        if untrusted:
            reasons.append(f"Untrusted skills blocked: {', '.join(untrusted)}")
            return SafetyDecision(
                risk_level=risk_level,
                blocked=True,
                requires_confirmation=False,
                use_dry_run=True,
                reasons=reasons,
            )

        requires_confirmation = risk_level == RiskLevel.HIGH and self._settings.high_risk_requires_confirmation
        if requires_confirmation and not confirmed:
            reasons.append("High-risk action requires explicit confirmation.")

        use_dry_run = dry_run_requested or (
            risk_level == RiskLevel.MEDIUM and self._settings.medium_risk_dry_run
        )
        if use_dry_run:
            reasons.append("Dry-run mode enabled.")

        blocked = requires_confirmation and not confirmed
        return SafetyDecision(
            risk_level=risk_level,
            blocked=blocked,
            requires_confirmation=requires_confirmation,
            use_dry_run=use_dry_run,
            reasons=reasons,
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        raw_tokens = re.findall(r"[\w/-]+", text.lower(), flags=re.UNICODE)
        normalized: set[str] = set()
        for token in raw_tokens:
            cleaned = token.strip("_-/")
            if not cleaned:
                continue
            normalized.add(cleaned)
            if cleaned.startswith(("و", "ف")) and len(cleaned) > 3:
                normalized.add(cleaned[1:])
            if cleaned.startswith("ال") and len(cleaned) > 4:
                normalized.add(cleaned[2:])
        return normalized
