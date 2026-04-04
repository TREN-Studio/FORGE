from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class OperatorSettings:
    """Configuration for the skill-based operator runtime."""

    package_root: Path = Path(__file__).resolve().parents[1]
    workspace_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    bundled_skills_root: Path = Path(__file__).resolve().parents[1] / "skills_catalog"
    external_skill_roots: list[Path] = field(default_factory=list)
    max_plan_steps: int = 5
    max_retries_per_step: int = 2
    max_fallback_skills: int = 3
    allow_untrusted_skills: bool = False
    medium_risk_dry_run: bool = True
    high_risk_requires_confirmation: bool = True
    enable_memory: bool = True
    memory_recall_limit: int = 6
    skill_score_threshold: float = 0.45
    artifact_dir_name: str = ".forge_artifacts"
    shell_timeout_seconds: int = 30
    shell_max_output_chars: int = 12000
    prompt_injection_max_chars: int = 6000
    browser_timeout_seconds: int = 20
    browser_snapshot_limit: int = 18
    browser_text_limit: int = 24
    browser_headless: bool = True

    @property
    def artifact_root(self) -> Path:
        return self.workspace_root / self.artifact_dir_name
