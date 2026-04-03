from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from forge.skills.contracts import SkillDefinition, SkillSource


REQUIRED_SECTIONS = {
    "purpose",
    "when to use",
    "when not to use",
    "inputs",
    "outputs",
    "execution rules",
    "validation",
    "safety",
    "failure modes",
    "fallback",
    "response style",
}


class SkillLoader:
    """Load skills from folders that follow the SKILL.md contract."""

    def load_directory(
        self,
        skill_dir: Path,
        source: SkillSource,
        trusted: bool,
    ) -> SkillDefinition | None:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return None

        front_matter, sections = self._parse_skill_markdown(skill_file.read_text(encoding="utf-8"))
        missing = REQUIRED_SECTIONS.difference(sections)
        if missing:
            raise ValueError(f"Skill `{skill_dir.name}` missing sections: {sorted(missing)}")

        schema_bundle = self._load_schema(skill_dir / "schema.json")
        executor = self._load_executor(skill_dir / "executor.py")

        return SkillDefinition(
            name=front_matter.get("name", skill_dir.name),
            description=front_matter.get("description", ""),
            category=front_matter.get("category", "general"),
            version=front_matter.get("version", "1.0.0"),
            path=skill_dir,
            purpose=self._collapse(sections["purpose"]),
            when_to_use=self._lines(sections["when to use"]),
            when_not_to_use=self._lines(sections["when not to use"]),
            inputs=self._lines(sections["inputs"]),
            outputs=self._lines(sections["outputs"]),
            execution_rules=self._lines(sections["execution rules"]),
            validation_rules=self._lines(sections["validation"]),
            safety_rules=self._lines(sections["safety"]),
            failure_modes=self._lines(sections["failure modes"]),
            fallback_notes=self._lines(sections["fallback"]),
            response_style=self._collapse(sections["response style"]),
            source=source,
            trusted=trusted,
            input_schema=schema_bundle.get("input_schema"),
            output_schema=schema_bundle.get("output_schema"),
            executor=executor,
            metadata=front_matter,
        )

    def _parse_skill_markdown(self, text: str) -> tuple[dict[str, str], dict[str, str]]:
        front_matter: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            _, remainder = text.split("---", 1)
            header_block, body = remainder.split("---", 1)
            for line in header_block.strip().splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                front_matter[key.strip().lower()] = value.strip()

        sections: dict[str, str] = {}
        current_section: str | None = None
        buffer: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.rstrip()
            if line.startswith("# "):
                if current_section:
                    sections[current_section] = "\n".join(buffer).strip()
                current_section = line[2:].strip().lower()
                buffer = []
                continue
            buffer.append(line)
        if current_section:
            sections[current_section] = "\n".join(buffer).strip()
        return front_matter, sections

    def _load_schema(self, schema_path: Path) -> dict[str, dict]:
        if not schema_path.exists():
            return {}
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _load_executor(self, executor_path: Path):
        if not executor_path.exists():
            return None

        module_name = f"forge_skill_{executor_path.parent.name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, executor_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load executor from {executor_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        executor = getattr(module, "execute", None)
        if executor is None:
            raise RuntimeError(f"Executor file {executor_path} must expose `execute`.")
        return executor

    @staticmethod
    def _lines(section: str) -> list[str]:
        return [
            line.lstrip("- ").strip()
            for line in section.splitlines()
            if line.strip()
        ]

    @staticmethod
    def _collapse(section: str) -> str:
        return " ".join(line.strip() for line in section.splitlines() if line.strip())
