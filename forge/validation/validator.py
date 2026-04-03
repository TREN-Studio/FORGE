from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.brain.contracts import CompletionState, ExecutionPlan, StepExecutionResult
from forge.skills.contracts import SkillDefinition


@dataclass(slots=True)
class ValidationResult:
    status: CompletionState
    notes: list[str] = field(default_factory=list)


class ResultValidator:
    """Validate step outputs and overall task completion."""

    def validate_step(
        self,
        skill: SkillDefinition | None,
        output: Any,
        expected_output: str,
        request: str,
    ) -> ValidationResult:
        notes: list[str] = []
        if output in (None, "", {}, []):
            return ValidationResult(status=CompletionState.FAILED, notes=["Output is empty."])

        if isinstance(output, dict) and output.get("status") == "dry_run":
            return ValidationResult(
                status=CompletionState.FINISHED,
                notes=["Dry run completed. Side effects were intentionally skipped."],
            )

        if isinstance(output, dict):
            artifact_path = output.get("artifact_path")
            if artifact_path and not Path(artifact_path).exists():
                return ValidationResult(
                    status=CompletionState.FAILED,
                    notes=["Artifact path was returned, but the file does not exist on disk."],
                )

            if "bytes_written" in output and output.get("bytes_written", 0) <= 0:
                return ValidationResult(
                    status=CompletionState.FAILED,
                    notes=["Artifact write reported zero bytes."],
                )

            if skill and str(skill.metadata.get("grounded", "false")).lower() == "true":
                evidence = output.get("evidence", [])
                files_reviewed = output.get("files_reviewed", [])
                if not evidence and not files_reviewed:
                    return ValidationResult(
                        status=CompletionState.PARTIALLY_FINISHED,
                        notes=["Grounding evidence is missing for an analysis-oriented skill."],
                    )

        if skill and skill.output_schema:
            schema_errors = self._validate_schema(skill.output_schema, output, "$")
            if schema_errors:
                return ValidationResult(status=CompletionState.FAILED, notes=schema_errors)

        text_view = str(output).strip().lower()
        if len(text_view) < 12:
            notes.append("Output is too short to be considered complete.")
            return ValidationResult(status=CompletionState.PARTIALLY_FINISHED, notes=notes)

        if "error" in text_view and "summary" not in text_view:
            notes.append("Output appears to contain an execution error.")
            return ValidationResult(status=CompletionState.FAILED, notes=notes)

        if expected_output and "validated" in expected_output.lower():
            notes.append("Basic completeness checks passed.")
        return ValidationResult(status=CompletionState.FINISHED, notes=notes)

    def evaluate_plan(
        self,
        plan: ExecutionPlan,
        step_results: list[StepExecutionResult],
    ) -> CompletionState:
        if not step_results:
            return CompletionState.FAILED
        if any(step.status == CompletionState.NEEDS_HUMAN_CONFIRMATION for step in step_results):
            return CompletionState.NEEDS_HUMAN_CONFIRMATION
        if any(step.status == CompletionState.NEEDS_RETRY for step in step_results):
            return CompletionState.NEEDS_RETRY
        if any(step.status == CompletionState.FAILED for step in step_results):
            if any(step.status == CompletionState.FINISHED for step in step_results):
                return CompletionState.PARTIALLY_FINISHED
            return CompletionState.FAILED
        if any(step.status == CompletionState.PARTIALLY_FINISHED for step in step_results):
            return CompletionState.PARTIALLY_FINISHED
        return CompletionState.FINISHED

    def _validate_schema(self, schema: dict[str, Any], value: Any, path: str) -> list[str]:
        errors: list[str] = []
        expected_type = schema.get("type")
        if expected_type == "object":
            if not isinstance(value, dict):
                return [f"{path} must be an object."]
            required = schema.get("required", [])
            for key in required:
                if key not in value:
                    errors.append(f"{path}.{key} is required.")
            properties = schema.get("properties", {})
            for key, subschema in properties.items():
                if key in value:
                    errors.extend(self._validate_schema(subschema, value[key], f"{path}.{key}"))
            return errors

        if expected_type == "array":
            if not isinstance(value, list):
                return [f"{path} must be an array."]
            item_schema = schema.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    errors.extend(self._validate_schema(item_schema, item, f"{path}[{index}]"))
            return errors

        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{path} must be a string.")
        if expected_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"{path} must be a number.")
        if expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{path} must be a boolean.")

        enum_values = schema.get("enum")
        if enum_values and value not in enum_values:
            errors.append(f"{path} must be one of: {', '.join(map(str, enum_values))}.")
        return errors
