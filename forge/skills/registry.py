from __future__ import annotations

from pathlib import Path

from forge.config.settings import OperatorSettings
from forge.skills.contracts import SkillDefinition, SkillSource
from forge.skills.loader import SkillLoader


class SkillRegistry:
    """Dynamic registry for bundled and external skills."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._loader = SkillLoader()
        self._skills: dict[str, SkillDefinition] = {}
        self._load_errors: dict[str, str] = {}
        self._trusted_external_skills: set[str] = set()

    def refresh(self) -> None:
        self._skills.clear()
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

    def list(self, trusted_only: bool = False) -> list[SkillDefinition]:
        skills = list(self._skills.values())
        if trusted_only:
            skills = [skill for skill in skills if skill.trusted]
        return sorted(skills, key=lambda skill: skill.name)

    @property
    def load_errors(self) -> dict[str, str]:
        return dict(self._load_errors)

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
            except Exception as exc:
                self._load_errors[child.name] = str(exc)
