from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any

from forge.config.settings import OperatorSettings
from forge.skills.contracts import SkillDefinition, SkillSource
from forge.skills.loader import SkillLoader


GATED_ENV_VAR = "FORGE_GATED"
GATED_TRUE_VALUES = {"1", "true", "yes", "on"}

READ_ONLY_INTENTS = {"analysis", "research", "conversation"}
WRITE_TERMS = {"write", "edit", "modify", "fix", "update", "append", "prepend", "replace", "patch", "create", "save"}
READ_TERMS = {"read", "inspect", "analyze", "analyse", "review", "summarize", "summarise", "extract", "identify"}
PATH_HINTS = ("/", "\\", ".py", ".ts", ".tsx", ".js", ".json", ".md", ".yml", ".yaml", ".toml", ".html", ".htm")


@dataclass(slots=True)
class SkillMeta:
    id: str
    tier: int
    risk_class: str
    visibility: str
    preconditions: list[str] = field(default_factory=list)
    allowlist: list[str] = field(default_factory=list)
    trigger_rules: list[str] = field(default_factory=list)
    built_in: bool = False


class SkillRegistry:
    """Dynamic registry for bundled and external skills."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._loader = SkillLoader()
        self._skills: dict[str, SkillDefinition] = {}
        self._skill_meta: dict[str, SkillMeta] = {}
        self._load_errors: dict[str, str] = {}
        self._trusted_external_skills: set[str] = set()

    def refresh(self) -> None:
        self._skills.clear()
        self._skill_meta.clear()
        self._load_errors.clear()
        self._load_root(self._settings.bundled_skills_root, SkillSource.BUNDLED, True)
        for root in self._settings.external_skill_roots:
            self._load_root(root, SkillSource.EXTERNAL, False)

    def trust_skill(self, skill_name: str) -> None:
        if skill_name in self._skills:
            self._skills[skill_name].trusted = True
            self._trusted_external_skills.add(skill_name)

    def get(self, skill_name: str) -> SkillDefinition | None:
        return self._skills.get(skill_name)

    def get_skill(self, skill_id: str, agent_type: str = "operator") -> SkillMeta | None:
        meta = self._skill_meta.get(skill_id)
        if meta is None:
            return None
        if not self._agent_allowed(meta, agent_type):
            return None
        if self._is_disabled_for_session(meta):
            return None
        return meta

    def skills_for_intent(self, intent: Any, agent_type: str = "operator") -> list[SkillMeta]:
        metas: list[SkillMeta] = []
        for skill_id in sorted(self._skill_meta):
            meta = self.get_skill(skill_id, agent_type=agent_type)
            if meta is None:
                continue
            if self._is_read_only_intent(intent) and meta.risk_class in {"write", "publish", "admin"}:
                continue
            if self._is_read_only_intent(intent) and meta.risk_class == "execute" and meta.id != "browser-executor":
                continue
            if not self._triggered_by_intent(meta, intent):
                continue
            if not self.preconditions_met(meta.id, intent):
                continue
            metas.append(meta)
        return metas

    def is_gated(self, skill_id: str) -> bool:
        meta = self._skill_meta.get(skill_id)
        if meta is None:
            return False
        return meta.tier >= 4 or meta.visibility in {"gated", "disabled"} or meta.risk_class == "admin"

    def preconditions_met(self, skill_id: str, intent: Any) -> bool:
        meta = self._skill_meta.get(skill_id)
        if meta is None:
            return True
        text = self._intent_text(intent)
        lowered = text.lower()
        for precondition in meta.preconditions:
            item = precondition.lower().strip()
            if item in {"request", "objective", "prior_results", "memory_context"}:
                continue
            if item in {"workspace", "directory_path"}:
                continue
            if item in {"file_path", "target_path", "repo_path", "release_path"} and not self._has_explicit_path(text):
                return False
            if item in {"new_content", "content", "payload"} and not self._has_explicit_content(text):
                return False
            if item in {"command", "test_path"} and not self._has_any_term(lowered, {"run", "execute", "command", "shell", "pytest", "test", "compile"}):
                return False
            if item in {"query_or_url", "start_url", "url", "site_url", "target_url"} and not self._has_url_or_query(text):
                return False
            if item in {"repo", "target_repo"} and not self._has_any_term(lowered, {"github", "repo", "repository"}):
                return False
            if item in {"authorization", "authorized_target", "scope", "oauth_token", "connection_string"}:
                return self._gated_enabled()
        return True

    def list(self, trusted_only: bool = False) -> list[SkillDefinition]:
        skills = list(self._skills.values())
        if trusted_only:
            skills = [skill for skill in skills if skill.trusted]
        return sorted(skills, key=lambda skill: skill.name)

    @property
    def load_errors(self) -> dict[str, str]:
        return dict(self._load_errors)

    @property
    def skill_meta(self) -> dict[str, SkillMeta]:
        return dict(self._skill_meta)

    def _load_root(self, root: Path, source: SkillSource, trusted: bool) -> None:
        if not root.exists():
            return

        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            try:
                definition = self._loader.load_directory(child, source=source, trusted=trusted)
                if definition is None:
                    continue
                if source == SkillSource.EXTERNAL and definition.name in self._trusted_external_skills:
                    definition.trusted = True
                self._skills[definition.name] = definition
                self._skill_meta[definition.name] = self._load_skill_meta(definition, child, source)
            except Exception as exc:
                self._load_errors[child.name] = str(exc)

    def _load_skill_meta(self, definition: SkillDefinition, skill_dir: Path, source: SkillSource) -> SkillMeta:
        schema_path = skill_dir / "schema.json"
        data: dict[str, Any] = {}
        if schema_path.exists():
            data = json.loads(schema_path.read_text(encoding="utf-8"))
        return SkillMeta(
            id=str(data.get("id") or definition.name),
            tier=int(data.get("tier") or self._default_tier(definition)),
            risk_class=str(data.get("risk_class") or self._default_risk_class(definition)).lower(),
            visibility=str(data.get("visibility") or self._default_visibility(definition)).lower(),
            preconditions=self._list_field(data.get("preconditions")),
            allowlist=self._list_field(data.get("allowlist")) or ["operator", "desktop"],
            trigger_rules=self._list_field(data.get("trigger_rules")) or definition.when_to_use,
            built_in=bool(data.get("built_in", source == SkillSource.BUNDLED)),
        )

    @staticmethod
    def _list_field(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    @staticmethod
    def _default_tier(definition: SkillDefinition) -> int:
        if "publish" in definition.category.lower() or "publisher" in definition.name.lower():
            return 2
        return 1

    @staticmethod
    def _default_risk_class(definition: SkillDefinition) -> str:
        searchable = definition.searchable_text
        name = definition.name.lower()
        if "publisher" in name or "publish" in searchable:
            return "publish"
        if "shell" in name or "execute" in searchable:
            return "execute"
        if "editor" in name or "writer" in name or "write" in searchable:
            return "write"
        return "read"

    @staticmethod
    def _default_visibility(definition: SkillDefinition) -> str:
        if "publisher" in definition.name.lower():
            return "on_request"
        return "always"

    @staticmethod
    def _gated_enabled() -> bool:
        return os.environ.get(GATED_ENV_VAR, "").strip().lower() in GATED_TRUE_VALUES

    def _is_disabled_for_session(self, meta: SkillMeta) -> bool:
        if meta.tier >= 4 and not self._gated_enabled():
            return True
        if meta.visibility in {"disabled", "gated"} and not self._gated_enabled():
            return True
        return False

    @staticmethod
    def _agent_allowed(meta: SkillMeta, agent_type: str) -> bool:
        allowlist = {item.lower() for item in meta.allowlist}
        return not allowlist or "*" in allowlist or agent_type.lower() in allowlist

    def _triggered_by_intent(self, meta: SkillMeta, intent: Any) -> bool:
        text = self._intent_text(intent).lower()
        if not text:
            return False
        if meta.id == "browser-executor" and ("http://" in text or "https://" in text or "file://" in text):
            return True
        for rule in meta.trigger_rules:
            rule_text = rule.lower()
            if rule_text in text:
                return True
            rule_tokens = set(re.findall(r"[\w/-]+", rule_text, flags=re.UNICODE))
            text_tokens = set(re.findall(r"[\w/-]+", text, flags=re.UNICODE))
            if len(rule_tokens) == 1 and rule_tokens.intersection(text_tokens):
                return True
            if len(rule_tokens) > 1 and rule_tokens.issubset(text_tokens):
                return True
        return False

    def _is_read_only_intent(self, intent: Any) -> bool:
        text = self._intent_text(intent).lower()
        if self._has_any_term(text, WRITE_TERMS):
            return False
        intent_values = self._intent_values(intent)
        if intent_values and all(item in READ_ONLY_INTENTS for item in intent_values):
            return True
        return self._has_any_term(text, READ_TERMS) and not self._has_any_term(text, WRITE_TERMS)

    @staticmethod
    def _intent_values(intent: Any) -> set[str]:
        values: set[str] = set()
        primary = getattr(intent, "primary_intent", None)
        if primary is not None:
            values.add(str(getattr(primary, "value", primary)).lower())
        for item in getattr(intent, "intents", []) or []:
            values.add(str(getattr(item, "value", item)).lower())
        return values

    @staticmethod
    def _intent_text(intent: Any) -> str:
        parts = [
            getattr(intent, "raw_request", ""),
            getattr(intent, "objective", ""),
            getattr(intent, "requested_output", ""),
            " ".join(getattr(intent, "notes", []) or []),
        ]
        return " ".join(part for part in parts if part)

    @staticmethod
    def _has_explicit_path(text: str) -> bool:
        lowered = text.lower()
        return any(hint in lowered for hint in PATH_HINTS)

    @staticmethod
    def _has_explicit_content(text: str) -> bool:
        lowered = text.lower()
        content_markers = (
            "```",
            "with content",
            "exactly this content",
            "content:",
            "new_content",
            "replace with",
            "append ",
            "prepend ",
        )
        return any(marker in lowered for marker in content_markers)

    @staticmethod
    def _has_url_or_query(text: str) -> bool:
        lowered = text.lower()
        return "http://" in lowered or "https://" in lowered or bool(re.search(r"\b(search|find|look up|query)\b", lowered))

    @staticmethod
    def _has_any_term(text: str, terms: set[str]) -> bool:
        tokens = set(re.findall(r"[\w/-]+", text.lower(), flags=re.UNICODE))
        normalized = {term.lower() for term in terms if term}
        return bool(tokens.intersection(normalized))
