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
        workspace_root: Path | None = None,
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

            edited_path = output.get("edited_path")
            if edited_path:
                candidate = Path(edited_path)
                if not candidate.exists():
                    if workspace_root is not None:
                        candidate = workspace_root / edited_path
                    else:
                        candidate = Path.cwd() / edited_path
                if not candidate.exists():
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["Edited file was reported, but the target does not exist on disk."],
                    )
                if not output.get("diff"):
                    status = CompletionState.PARTIALLY_FINISHED if output.get("changed") is False else CompletionState.FAILED
                    return ValidationResult(
                        status=status,
                        notes=["File edit returned no diff output."],
                    )

            if output.get("command"):
                exit_code = output.get("exit_code")
                if exit_code is not None and int(exit_code) != 0:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=[f"Command exited with non-zero status: {exit_code}."],
                    )
                if not output.get("stdout") and not output.get("stderr") and output.get("status") != "dry_run":
                    return ValidationResult(
                        status=CompletionState.PARTIALLY_FINISHED,
                        notes=["Command completed with no captured output."],
                    )

            if output.get("target_url"):
                response_status = output.get("response_status")
                if response_status is None:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["External publish returned no HTTP status."],
                    )
                if int(response_status) >= 400:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=[f"External publish failed with HTTP status {response_status}."],
                    )
                if int(output.get("published_bytes", 0)) <= 0:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["External publish reported zero published bytes."],
                    )

            if output.get("provider") == "github":
                if not output.get("repository") or not output.get("repo_path"):
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["GitHub publish is missing repository or repo path metadata."],
                    )
                if int(output.get("response_status", 0)) >= 400:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=[f"GitHub publish failed with HTTP status {output.get('response_status')}."],
                    )
                if int(output.get("published_bytes", 0)) <= 0:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["GitHub publish reported zero published bytes."],
                    )
                if output.get("status") != "dry_run" and not output.get("commit_sha"):
                    return ValidationResult(
                        status=CompletionState.PARTIALLY_FINISHED,
                        notes=["GitHub publish completed without a commit SHA."],
                    )

            if output.get("provider") == "wordpress":
                if not output.get("site_url") or not output.get("resource_type"):
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["WordPress publish is missing site or resource metadata."],
                    )
                if int(output.get("response_status", 0)) >= 400:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=[f"WordPress publish failed with HTTP status {output.get('response_status')}."],
                    )
                if int(output.get("published_bytes", 0)) <= 0:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["WordPress publish reported zero published bytes."],
                    )
                if output.get("status") != "dry_run" and not output.get("resource_id"):
                    return ValidationResult(
                        status=CompletionState.PARTIALLY_FINISHED,
                        notes=["WordPress publish completed without a resource id."],
                    )

            if output.get("page_state") is not None or output.get("snapshot_text"):
                current_url = str(output.get("current_url", "")).strip()
                if not current_url:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["Browser execution returned no current URL."],
                    )
                page_state = output.get("page_state") or {}
                semantic_count = 0
                if isinstance(page_state, dict):
                    semantic_count = sum(len(value) for value in page_state.values() if isinstance(value, list))
                if semantic_count <= 0 and not str(output.get("snapshot_text", "")).strip():
                    return ValidationResult(
                        status=CompletionState.PARTIALLY_FINISHED,
                        notes=["Browser execution returned no semantic page content."],
                    )

            if output.get("fanout_results"):
                results = output.get("fanout_results")
                if not isinstance(results, list) or not results:
                    return ValidationResult(
                        status=CompletionState.FAILED,
                        notes=["Browser fan-out returned no child results."],
                    )
                if any(not isinstance(item, dict) or not item.get("current_url") for item in results):
                    return ValidationResult(
                        status=CompletionState.PARTIALLY_FINISHED,
                        notes=["At least one browser fan-out child result is missing URL evidence."],
                    )

            if skill and str(skill.metadata.get("grounded", "false")).lower() == "true":
                evidence = output.get("evidence", [])
                files_reviewed = output.get("files_reviewed", [])
                if (
                    not evidence
                    and not files_reviewed
                    and not output.get("command")
                    and not output.get("edited_path")
                    and not output.get("current_url")
                ):
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
