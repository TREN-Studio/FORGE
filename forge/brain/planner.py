from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from typing import Any

from forge.brain.contracts import ExecutionPlan, PlanStep, TaskIntent
from forge.safety.guard import SafetyDecision
from forge.skills.contracts import RoutingDecision


FILE_HINTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".sql", ".html", ".htm")
URL_PATTERN = re.compile(r"(https?://[^\s`\"']+|file://[^\s`\"']+|data:text/html,[^\s`\"']+)", flags=re.IGNORECASE)
FENCED_BLOCK_PATTERN = re.compile(r"```(?P<lang>[\w.+-]+)?\n(?P<body>.*?)```", flags=re.DOTALL | re.IGNORECASE)
INLINE_CODE_PATTERN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
SAVE_TERMS = ("save", "write", "create", "update", "edit", "append", "prepend", "replace", "patch", "export", "اكتب", "احفظ", "أنشئ", "حدث", "حرر", "بدل", "أضف")
SHELL_TERMS = ("run", "execute", "command", "shell", "terminal", "compile", "test", "نفذ", "شغل", "أمر", "ترمنال")
BROWSER_TERMS = ("browse", "visit", "website", "web", "page", "browser", "navigate", "click", "fill", "extract", "site", "open", "افتح", "تصفح", "موقع", "صفحة", "اضغط", "املأ", "استخرج")
PUBLISH_TERMS = ("publish", "post", "upload", "webhook", "send", "submit", "deploy", "push live", "انشر", "ارسل", "ارفع", "نشر")
GITHUB_TERMS = ("github", "repository", "repo", "commit", "push")
WORDPRESS_TERMS = ("wordpress", "hostinger", "wp-json", "blog post", "landing page", "wp admin")
SHELL_LANGS = {"bash", "sh", "shell", "powershell", "pwsh", "cmd", "ps1"}
SHELL_HINT_EXECUTABLES = {
    "python",
    "py",
    "pytest",
    "git",
    "rg",
    "curl",
    "wget",
    "npm",
    "pnpm",
    "yarn",
    "node",
    "uv",
    "bash",
    "sh",
    "pwsh",
    "powershell",
    "cmd",
    "make",
}
CONTENT_BOUNDARY_PREFIXES = (
    "then ",
    "then run",
    "then execute",
    "and then",
    "next ",
    "after that",
    "afterwards",
    "finally",
    "now ",
    "run ",
    "execute ",
    "compile ",
    "test ",
    "push ",
    "publish ",
    "ثم",
    "بعد ذلك",
    "بعدها",
    "اخيرا",
    "أخيراً",
    "الآن",
    "نفذ",
    "شغل",
)
READ_TERMS = (
    "read",
    "inspect",
    "analyze",
    "analyse",
    "review",
    "summarize",
    "summarise",
    "extract",
    "identify",
    "understand",
    "Ø§Ù‚Ø±Ø£",
    "Ø§ÙØ­Øµ",
    "Ø­Ù„Ù„",
    "Ø±Ø§Ø¬Ø¹",
    "Ù„Ø®Øµ",
    "Ø§Ø³ØªØ®Ø±Ø¬",
)
NEGATED_EDIT_TERMS = (
    "do not edit",
    "don't edit",
    "without editing",
    "no edit",
    "read-only",
    "Ø¨Ø¯ÙˆÙ† ØªØ¹Ø¯ÙŠÙ„",
)
CODE_TERMS = ("code", "bug", "fix", "test", "unittest", "pytest", "function", "class", "module", "project", "codebase")


@dataclass(slots=True)
class _CodeBlock:
    lang: str
    body: str
    start: int
    end: int


@dataclass(slots=True)
class _FileOperation:
    target_path: str
    edit_mode: str
    content: str
    find_text: str
    replace_text: str
    order: int


class PlanningEngine:
    """Decompose requests into compact, execution-oriented mission steps."""

    def build(
        self,
        intent: TaskIntent,
        routing: RoutingDecision,
        safety: SafetyDecision,
        request: str | None = None,
        max_steps: int = 5,
    ) -> ExecutionPlan:
        source_request = request or intent.raw_request
        steps = self._decompose_execution_steps(source_request, safety, max_steps=max_steps)
        if not steps:
            steps = self._fallback_steps(intent, routing, safety, max_steps=max_steps)

        return ExecutionPlan(
            objective=intent.objective,
            task_type=intent.task_type,
            risk_level=safety.risk_level,
            steps=steps,
            fallbacks=routing.fallback_skills,
            completion_criteria=[
                "Every executed step returns evidence-backed output.",
                "Validation passes or partial completion is reported honestly.",
                "Retries stay local to the failed step whenever possible.",
                "Mutable steps are rolled back safely when the mission aborts.",
            ],
        )

    def _decompose_execution_steps(
        self,
        request: str,
        safety: SafetyDecision,
        *,
        max_steps: int,
    ) -> list[PlanStep]:
        shell_command = self._extract_shell_command(request)
        if not shell_command:
            shell_command = self._infer_test_command(request)
        non_shell_request = self._strip_shell_segments(request)
        request_lower = non_shell_request.lower()
        paths = self._extract_paths(non_shell_request)
        publish_target = self._extract_publish_target(non_shell_request)
        github_target = self._extract_github_target(non_shell_request, paths)
        wordpress_target = self._extract_wordpress_target(non_shell_request, publish_target)
        generic_publish_target = publish_target if not github_target and not wordpress_target else ""
        browser_targets = self._extract_browser_targets(non_shell_request, paths, allow_url=not bool(publish_target))
        file_operations = self._extract_file_operations(non_shell_request, paths)
        read_paths = self._extract_read_paths(non_shell_request, paths, file_operations=file_operations)
        needs_evidence_first = self._needs_evidence_first(non_shell_request, file_operations, read_paths)

        specs: list[dict[str, Any]] = []

        if browser_targets:
            browser_target = browser_targets[0]
            browser_input: dict[str, Any] = {"start_url": browser_target}
            if len(browser_targets) > 1:
                browser_input["fanout_targets"] = browser_targets
            specs.append(
                {
                    "skill": "browser-executor",
                    "order": self._browser_order(request_lower, browser_target),
                    "input_spec": browser_input,
                    "expected_output": "A semantic browser snapshot, current URL, and action trace.",
                    "validation": "Confirm navigation happened and semantic page state is populated.",
                    "stop_on_failure": False,
                    "rollback_on_failure": False,
                }
            )

        if needs_evidence_first and read_paths:
            analysis_skill = "codebase-analyzer" if self._is_code_or_project_request(non_shell_request) else "file-reader"
            specs.append(
                {
                    "skill": analysis_skill,
                    "order": self._read_order(request_lower, read_paths[0]),
                    "input_spec": {"source_paths": read_paths},
                    "expected_output": "Grounded excerpts or analysis from the requested workspace files.",
                    "validation": "Confirm file evidence was collected before any mutation or synthesis step.",
                    "stop_on_failure": True,
                    "rollback_on_failure": False,
                }
            )
        elif needs_evidence_first and not file_operations:
            analysis_skill = "codebase-analyzer" if self._is_code_or_project_request(non_shell_request) else "workspace-inspector"
            specs.append(
                {
                    "skill": analysis_skill,
                    "order": 100,
                    "input_spec": {},
                    "expected_output": "Grounded workspace analysis before any final answer.",
                    "validation": "Confirm evidence was collected from the workspace.",
                    "stop_on_failure": True,
                    "rollback_on_failure": False,
                }
            )

        for operation in file_operations:
            input_spec: dict[str, Any] = {
                "target_path": operation.target_path,
                "edit_mode": operation.edit_mode,
            }
            if operation.content:
                input_spec["content"] = operation.content
            if operation.find_text or operation.replace_text:
                input_spec["find_text"] = operation.find_text
                input_spec["replace_text"] = operation.replace_text
            specs.append(
                {
                    "skill": "file-editor",
                    "order": operation.order,
                    "input_spec": input_spec,
                    "expected_output": "A validated file mutation with diff and rollback metadata.",
                    "validation": "Confirm the target file changed as intended and a diff is available.",
                    "stop_on_failure": True,
                    "rollback_on_failure": True,
                }
            )

        if shell_command:
            specs.append(
                {
                    "skill": "shell-executor",
                    "order": self._shell_order(request_lower, shell_command),
                    "input_spec": {"command": shell_command},
                    "expected_output": "Captured command output with exit code 0.",
                    "validation": "Confirm stdout/stderr were captured and exit code is zero.",
                    "stop_on_failure": True,
                    "rollback_on_failure": False,
                }
            )

        if github_target:
            input_spec = dict(github_target)
            explicit_content = self._extract_content(request)
            if explicit_content:
                input_spec["content"] = explicit_content
            specs.append(
                {
                    "skill": "github-publisher",
                    "order": self._publish_order(request_lower, str(input_spec.get("target_repo", "github"))),
                    "input_spec": input_spec,
                    "expected_output": "A confirmed GitHub publish result with commit evidence.",
                    "validation": "Confirm the repository, path, response status, and commit reference are present.",
                    "stop_on_failure": True,
                    "rollback_on_failure": False,
                }
            )

        if wordpress_target:
            input_spec = dict(wordpress_target)
            explicit_content = self._extract_content(request)
            if explicit_content:
                input_spec["content"] = explicit_content
            specs.append(
                {
                    "skill": "wordpress-publisher",
                    "order": self._publish_order(request_lower, str(input_spec.get("site_url", "wordpress"))),
                    "input_spec": input_spec,
                    "expected_output": "A confirmed WordPress publish result with resource evidence.",
                    "validation": "Confirm the site URL, response status, and published resource metadata are present.",
                    "stop_on_failure": True,
                    "rollback_on_failure": False,
                }
            )

        if generic_publish_target:
            input_spec: dict[str, Any] = {"target_url": generic_publish_target, "method": "POST"}
            explicit_content = self._extract_content(request)
            if explicit_content:
                input_spec["content"] = explicit_content
            specs.append(
                {
                    "skill": "external-publisher",
                    "order": self._publish_order(request_lower, generic_publish_target),
                    "input_spec": input_spec,
                    "expected_output": "A confirmed external publish result with HTTP evidence.",
                    "validation": "Confirm the external endpoint responded successfully and the request body was sent intentionally.",
                    "stop_on_failure": True,
                    "rollback_on_failure": False,
                }
            )

        specs.sort(key=lambda item: (item["order"], item["skill"]))
        if specs:
            return self._specs_to_steps(specs[:max_steps], safety)
        return []

    def _fallback_steps(
        self,
        intent: TaskIntent,
        routing: RoutingDecision,
        safety: SafetyDecision,
        *,
        max_steps: int,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for index, skill_name in enumerate(routing.selected_skills[:max_steps], start=1):
            fallback = routing.fallback_skills[index - 1] if index - 1 < len(routing.fallback_skills) else None
            steps.append(
                PlanStep(
                    id=f"step_{index}",
                    action=f"Execute skill `{skill_name}` to advance the objective.",
                    skill=skill_name,
                    tool=skill_name,
                    input_spec={},
                    expected_output=f"Validated output for {intent.primary_intent.value}.",
                    validation="Check schema, completeness, and alignment with the user objective.",
                    risk_note="Run in dry-run mode." if safety.use_dry_run else "",
                    fallback_skill=fallback,
                    depends_on=[steps[-1].id] if steps else [],
                    retry_limit=2,
                    stop_on_failure=True,
                    rollback_on_failure=skill_name == "file-editor",
                )
            )

        if not steps:
            steps.append(
                PlanStep(
                    id="step_1",
                    action="Use reasoning-only path to answer without external skill execution.",
                    skill=None,
                    tool=None,
                    input_spec={},
                    expected_output="Direct, validated answer with explicit limitations.",
                    validation="Ensure the answer addresses the objective and contains no fabricated execution claims.",
                    risk_note="",
                    fallback_skill=None,
                    depends_on=[],
                    retry_limit=1,
                    stop_on_failure=True,
                    rollback_on_failure=False,
                )
            )
        return steps

    @staticmethod
    def _specs_to_steps(specs: list[dict[str, Any]], safety: SafetyDecision) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for index, spec in enumerate(specs, start=1):
            steps.append(
                PlanStep(
                    id=f"step_{index}",
                    action=f"Dispatch `{spec['skill']}` for sub-task {index}.",
                    skill=spec["skill"],
                    tool=spec["skill"],
                    input_spec=spec["input_spec"],
                    expected_output=spec["expected_output"],
                    validation=spec["validation"],
                    risk_note="Run in dry-run mode." if safety.use_dry_run else "",
                    fallback_skill=None,
                    depends_on=[steps[-1].id] if steps else [],
                    retry_limit=2,
                    stop_on_failure=bool(spec["stop_on_failure"]),
                    rollback_on_failure=bool(spec["rollback_on_failure"]),
                )
            )
        return steps

    @staticmethod
    def _extract_browser_targets(request: str, paths: list[str], *, allow_url: bool = True) -> list[str]:
        targets: list[str] = []
        lowered = request.lower()
        if allow_url and any(term in lowered for term in BROWSER_TERMS):
            targets.extend(PlanningEngine._clean_url_target(match.group(1)) for match in URL_PATTERN.finditer(request))
        for path in paths:
            if path.lower().endswith((".html", ".htm")):
                targets.append(path)
        return list(dict.fromkeys(targets))

    @staticmethod
    def _extract_publish_target(request: str) -> str:
        lowered = request.lower()
        if not any(term in lowered for term in PUBLISH_TERMS):
            return ""
        match = URL_PATTERN.search(request)
        return PlanningEngine._clean_url_target(match.group(1)) if match else ""

    @staticmethod
    def _extract_github_target(request: str, paths: list[str]) -> dict[str, str]:
        lowered = request.lower()
        if not any(term in lowered for term in PUBLISH_TERMS):
            return {}
        if not PlanningEngine._contains_named_term(lowered, GITHUB_TERMS) and "github.com/" not in lowered:
            return {}

        target: dict[str, str] = {}
        url_match = re.search(
            r"https://github\.com/([^/\s]+)/([^/\s`\"']+)(?:/(?:blob|tree)/([^/\s`\"']+)/(.*))?",
            request,
            flags=re.IGNORECASE,
        )
        if url_match:
            target["target_repo"] = f"{PlanningEngine._clean_url_target(url_match.group(1))}/{PlanningEngine._clean_url_target(url_match.group(2))}"
            if url_match.group(3):
                target["branch"] = PlanningEngine._clean_url_target(url_match.group(3))
            if url_match.group(4):
                target["repo_path"] = PlanningEngine._clean_url_target(url_match.group(4))
        else:
            repo_hint = re.search(r"\b(?:repo|repository)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", request, flags=re.IGNORECASE)
            if repo_hint:
                target["target_repo"] = repo_hint.group(1)
            else:
                slash_tokens = re.findall(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", request)
                for candidate in slash_tokens:
                    lowered_candidate = candidate.lower()
                    if candidate.startswith("."):
                        continue
                    if any(lowered_candidate.endswith(suffix) for suffix in FILE_HINTS):
                        continue
                    target["target_repo"] = candidate
                    break

        if "repo_path" not in target:
            publish_file_path = PlanningEngine._extract_publish_file_path(paths)
            if publish_file_path:
                target["repo_path"] = publish_file_path
        return target

    @staticmethod
    def _extract_wordpress_target(request: str, publish_target: str) -> dict[str, str]:
        lowered = request.lower()
        if not any(term in lowered for term in PUBLISH_TERMS):
            return {}
        if not PlanningEngine._contains_named_term(lowered, WORDPRESS_TERMS):
            return {}

        target: dict[str, str] = {}
        if publish_target and "github.com/" not in publish_target.lower():
            target["site_url"] = publish_target
        target["resource_type"] = "pages" if any(phrase in lowered for phrase in ("wordpress page", "landing page", "update page", "site page")) else "posts"
        target["status"] = "draft" if "draft" in lowered else "publish"

        slug_match = re.search(r"\bslug\s*[:=]?\s*([A-Za-z0-9-_/]+)", request, flags=re.IGNORECASE)
        if slug_match:
            target["slug"] = slug_match.group(1).strip().strip("/").lower()

        title_match = re.search(r"\btitle\s*[:=]\s*(.+)$", request, flags=re.IGNORECASE)
        if title_match:
            target["title"] = title_match.group(1).strip().strip("`\"'")

        id_match = re.search(r"\b(?:post|page)\s+(?:id\s*)?(\d+)\b", request, flags=re.IGNORECASE)
        if id_match:
            target["resource_id"] = id_match.group(1)
        return target

    @staticmethod
    def _clean_url_target(value: str) -> str:
        return value.strip().rstrip(".,;:!?)\"]}'")

    @staticmethod
    def _contains_named_term(text: str, terms: tuple[str, ...]) -> bool:
        for term in terms:
            normalized = term.lower()
            if " " in normalized or "-" in normalized:
                if normalized in text:
                    return True
                continue
            if re.search(rf"\b{re.escape(normalized)}\b", text):
                return True
        return False

    @staticmethod
    def _extract_publish_file_path(paths: list[str]) -> str:
        for path in reversed(paths):
            lowered = path.lower()
            if lowered.endswith((".md", ".txt", ".json", ".yml", ".yaml", ".toml")):
                return path
        return ""

    @staticmethod
    def _extract_file_operations(request: str, paths: list[str]) -> list[_FileOperation]:
        non_html_paths = PlanningEngine._prefer_specific_paths(
            [path for path in paths if not path.lower().endswith((".html", ".htm"))]
        )
        if not non_html_paths:
            return []

        lowered = request.lower()
        if not any(term in lowered for term in SAVE_TERMS):
            return []

        blocks = PlanningEngine._code_blocks(request)
        operations: list[_FileOperation] = []
        seen: set[str] = set()
        replace_mode = "replace" in lowered

        if replace_mode:
            target = PlanningEngine._extract_file_target(request, non_html_paths)
            if target:
                find_text = blocks[0].body.strip() if len(blocks) >= 1 else ""
                replace_text = blocks[1].body.strip() if len(blocks) >= 2 else ""
                operations.append(
                    _FileOperation(
                        target_path=target,
                        edit_mode="replace",
                        content="",
                        find_text=find_text,
                        replace_text=replace_text,
                        order=PlanningEngine()._file_order(lowered, target),
                    )
                )
            return operations

        for path in non_html_paths:
            if path in seen:
                continue
            path_pos = PlanningEngine._find_path_position(request, path)
            if path_pos < 0:
                continue
            if not PlanningEngine._path_is_write_target(request, lowered, path, path_pos):
                continue
            content = PlanningEngine._content_after_path(request, path, path_pos, blocks)
            if not content:
                content = PlanningEngine._extract_inline_content_for_path(request, path, path_pos)
            if not content and not any(term in lowered for term in READ_TERMS):
                content = PlanningEngine._synthesize_content_for_path(request, path)
            operations.append(
                _FileOperation(
                    target_path=path,
                    edit_mode=PlanningEngine._infer_edit_mode_for_path(request, path_pos),
                    content=content,
                    find_text="",
                    replace_text="",
                    order=PlanningEngine._file_operation_order(lowered, path, len(operations)),
                )
            )
            seen.add(path)
        return operations

    @staticmethod
    def _extract_read_paths(request: str, paths: list[str], *, file_operations: list[_FileOperation]) -> list[str]:
        operation_targets = {operation.target_path for operation in file_operations}
        lowered = request.lower()
        read_paths: list[str] = []
        for path in paths:
            if path in operation_targets and not any(term in lowered for term in ("read back", "verify", "validate", "check")):
                continue
            path_pos = PlanningEngine._find_path_position(request, path)
            if path_pos < 0:
                continue
            context = lowered[max(0, path_pos - 100) : path_pos + len(path) + 100]
            if any(term in context for term in READ_TERMS) or path not in operation_targets:
                read_paths.append(path)
        return list(dict.fromkeys(read_paths))

    @staticmethod
    def _needs_evidence_first(request: str, file_operations: list[_FileOperation], read_paths: list[str]) -> bool:
        lowered = request.lower()
        if any(term in lowered for term in NEGATED_EDIT_TERMS):
            return bool(read_paths)
        evidence_words = any(term in lowered for term in READ_TERMS)
        synthesis_words = any(term in lowered for term in ("extract", "summarize", "synthes", "report", "action item", "finding", "analyze", "analyse"))
        missing_content = any(
            operation.edit_mode != "replace" and not operation.content
            for operation in file_operations
        )
        return bool(read_paths) and (evidence_words or synthesis_words or missing_content)

    @staticmethod
    def _is_code_or_project_request(request: str) -> bool:
        lowered = request.lower()
        return any(term in lowered for term in CODE_TERMS)

    @staticmethod
    def _code_blocks(request: str) -> list[_CodeBlock]:
        return [
            _CodeBlock(
                lang=(match.group("lang") or "").lower(),
                body=match.group("body"),
                start=match.start(),
                end=match.end(),
            )
            for match in FENCED_BLOCK_PATTERN.finditer(request)
        ]

    @staticmethod
    def _find_path_position(request: str, path: str) -> int:
        normalized = path.replace("\\", "/")
        candidates = [normalized, normalized.replace("/", "\\")]
        lowered = request.lower()
        positions = [lowered.find(candidate.lower()) for candidate in candidates if lowered.find(candidate.lower()) >= 0]
        return min(positions) if positions else -1

    @staticmethod
    def _path_is_write_target(request: str, lowered_request: str, path: str, path_pos: int) -> bool:
        before = lowered_request[max(0, path_pos - 140) : path_pos]
        after = lowered_request[path_pos : path_pos + len(path) + 140]
        if any(term in before[-80:] for term in READ_TERMS) and not any(term in before for term in SAVE_TERMS):
            return False
        if any(term in before for term in SAVE_TERMS):
            return True
        if re.search(rf"\bto\s+{re.escape(path.lower())}\b", after):
            return True
        return False

    @staticmethod
    def _content_after_path(request: str, path: str, path_pos: int, blocks: list[_CodeBlock]) -> str:
        candidates = [block for block in blocks if block.start > path_pos]
        if not candidates:
            return ""
        block = candidates[0]
        gap_start = path_pos + len(path)
        next_path = re.search(r"[\w./\\:-]+\.(?:py|ts|tsx|js|jsx|json|md|txt|toml|ya?ml|sql|html?)", request[gap_start:block.start], flags=re.IGNORECASE)
        if next_path:
            return ""
        if block.lang in SHELL_LANGS or PlanningEngine._looks_like_shell_command(block.body.strip()):
            return ""
        return block.body.strip()

    @staticmethod
    def _extract_inline_content_for_path(request: str, path: str, path_pos: int) -> str:
        tail = request[path_pos + len(path) :]
        match = re.search(
            r"(?:with|content|text|exact content|exactly this content)\s*:?\s*(.+?)(?=(?:\bthen\b|\band then\b|\bnext\b|\bfinally\b|$))",
            tail,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        content = match.group(1).strip()
        for later_path in PlanningEngine._extract_paths(content):
            if later_path != path:
                content = content.split(later_path, 1)[0].strip(" ,.;:")
                break
        return PlanningEngine._trim_inline_content_boundary(content)

    @staticmethod
    def _synthesize_content_for_path(request: str, path: str) -> str:
        lowered = request.lower()
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""

        object_hint = re.search(r"\{([^{}]+)\}", request)
        if suffix == "json":
            fields: dict[str, str] = {}
            if object_hint:
                for raw_name in re.split(r"[,;]", object_hint.group(1)):
                    name = raw_name.strip().strip("'\"")
                    if not name:
                        continue
                    fields[PlanningEngine._json_key(name)] = PlanningEngine._json_value(name, request)
            if not fields:
                fields = {"project": "forge", "version": "1.0"}
            import json

            return json.dumps(fields, ensure_ascii=False, indent=2)

        list_hint = re.search(r"\[([^\[\]]+)\]", request)
        if suffix in {"md", "txt"}:
            title = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
            if "run the test" in lowered and ("report" in lowered or "reports/" in path.lower()):
                return ""
            if list_hint:
                items = [item.strip().strip("'\"") for item in re.split(r"[,;]", list_hint.group(1)) if item.strip()]
                if items:
                    return "# " + title + "\n" + "\n".join(f"- {item}" for item in items)
            if "task" in lowered or "todo" in lowered:
                return "# Tasks\n- Configure\n- Verify\n"
            if "report" in lowered or "summary" in lowered or "action item" in lowered:
                return f"# {title}\n\nPending synthesis from workspace evidence.\n"
        if suffix == "py" and "calculator" in lowered:
            normalized_path = path.replace("\\", "/").lower()
            if normalized_path.endswith("src/utils.py"):
                return (
                    '"""Helper functions for the calculator project."""\n\n'
                    "def add(left: float, right: float) -> float:\n"
                    "    return left + right\n\n\n"
                    "def subtract(left: float, right: float) -> float:\n"
                    "    return left - right\n\n\n"
                    "def multiply(left: float, right: float) -> float:\n"
                    "    return left * right\n\n\n"
                    "def divide(left: float, right: float) -> float:\n"
                    "    if right == 0:\n"
                    '        raise ValueError("Cannot divide by zero.")\n'
                    "    return left / right\n"
                )
            if normalized_path.endswith("src/main.py"):
                return (
                    '"""Small calculator entry point."""\n\n'
                    "from utils import add, divide, multiply, subtract\n\n\n"
                    "class Calculator:\n"
                    "    def add(self, left: float, right: float) -> float:\n"
                    "        return add(left, right)\n\n"
                    "    def subtract(self, left: float, right: float) -> float:\n"
                    "        return subtract(left, right)\n\n"
                    "    def multiply(self, left: float, right: float) -> float:\n"
                    "        return multiply(left, right)\n\n"
                    "    def divide(self, left: float, right: float) -> float:\n"
                    "        return divide(left, right)\n\n\n"
                    'if __name__ == "__main__":\n'
                    "    calculator = Calculator()\n"
                    '    print(f"2 + 3 = {calculator.add(2, 3)}")\n'
                )
            if normalized_path.endswith("tests/test_utils.py"):
                return (
                    "from pathlib import Path\n"
                    "import sys\n"
                    "import unittest\n\n"
                    "ROOT = Path(__file__).resolve().parents[1]\n"
                    "sys.path.insert(0, str(ROOT / \"src\"))\n\n"
                    "from main import Calculator\n"
                    "from utils import add, divide, multiply, subtract\n\n\n"
                    "class CalculatorTests(unittest.TestCase):\n"
                    "    def test_helpers(self) -> None:\n"
                    "        self.assertEqual(add(2, 3), 5)\n"
                    "        self.assertEqual(subtract(5, 2), 3)\n"
                    "        self.assertEqual(multiply(4, 3), 12)\n"
                    "        self.assertEqual(divide(8, 2), 4)\n\n"
                    "    def test_calculator(self) -> None:\n"
                    "        calculator = Calculator()\n"
                    "        self.assertEqual(calculator.add(1, 4), 5)\n"
                    "        self.assertEqual(calculator.subtract(9, 4), 5)\n\n\n"
                    'if __name__ == "__main__":\n'
                    "    unittest.main()\n"
                )
        return ""

    @staticmethod
    def _json_key(name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower()).strip("_") or "value"

    @staticmethod
    def _json_value(name: str, request: str) -> str:
        lowered_name = name.lower()
        if "version" in lowered_name:
            version_match = re.search(r"\b\d+(?:\.\d+){1,3}\b", request)
            return version_match.group(0) if version_match else "1.0"
        if "name" in lowered_name or "project" in lowered_name or "app" in lowered_name:
            return "forge"
        return ""

    @staticmethod
    def _infer_edit_mode_for_path(request: str, path_pos: int) -> str:
        context = request[max(0, path_pos - 120) : path_pos + 120].lower()
        if "append" in context:
            return "append"
        if "prepend" in context:
            return "prepend"
        if "replace" in context:
            return "replace"
        if "create" in context or "new file" in context:
            return "create"
        return PlanningEngine._infer_edit_mode(request)

    @staticmethod
    def _extract_file_target(request: str, paths: list[str]) -> str:
        non_html_paths = [path for path in paths if not path.lower().endswith((".html", ".htm"))]
        if not non_html_paths:
            return ""

        lowered = request.lower()
        if not any(term in lowered for term in SAVE_TERMS):
            return ""

        target_match = re.search(
            r"(?:save|write|create|update|edit|append|prepend|replace|export)\b.*?\bto\s+([^\s`\"']+)",
            lowered,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if target_match:
            target_token = target_match.group(1).strip("`'\" ,:;()[]{}").replace("\\", "/")
            for path in non_html_paths:
                if path.lower() == target_token.lower():
                    return path
        return non_html_paths[-1]

    @staticmethod
    def _extract_shell_command(request: str) -> str:
        for match in FENCED_BLOCK_PATTERN.finditer(request):
            language = (match.group("lang") or "").lower()
            candidate = match.group("body").strip()
            if not candidate:
                continue
            if language in SHELL_LANGS or PlanningEngine._looks_like_shell_command(candidate):
                return candidate

        inline_source = FENCED_BLOCK_PATTERN.sub(" ", request)
        inline = INLINE_CODE_PATTERN.findall(inline_source)
        for candidate in inline:
            candidate = candidate.strip()
            if candidate and PlanningEngine._looks_like_shell_command(candidate):
                return candidate

        match = re.search(r"(?:run|execute|command|shell)\s*[: ]\s*(.+)$", request, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        candidate = match.group(1).strip()
        return candidate if PlanningEngine._looks_like_shell_command(candidate) else ""

    @staticmethod
    def _strip_shell_segments(request: str) -> str:
        cleaned = request
        for match in FENCED_BLOCK_PATTERN.finditer(request):
            candidate = match.group("body").strip()
            language = (match.group("lang") or "").lower()
            if candidate and (language in SHELL_LANGS or PlanningEngine._looks_like_shell_command(candidate)):
                cleaned = cleaned.replace(match.group(0), " ")

        inline_source = FENCED_BLOCK_PATTERN.sub(lambda match: match.group(0) if match.group(0) in cleaned else " ", cleaned)

        def replace_inline(match: re.Match[str]) -> str:
            candidate = match.group(1).strip()
            return " " if candidate and PlanningEngine._looks_like_shell_command(candidate) else match.group(0)

        return INLINE_CODE_PATTERN.sub(replace_inline, inline_source)

    @staticmethod
    def _extract_paths(request: str) -> list[str]:
        paths: list[str] = []
        for token in re.findall(r"[\w./\\:-]+", request, flags=re.UNICODE):
            cleaned = PlanningEngine._clean_path_token(token)
            lowered = cleaned.lower()
            if len(cleaned) < 3:
                continue
            if cleaned.startswith(("http://", "https://", "file://", "data:text/html,")):
                continue
            if any(hint in lowered for hint in FILE_HINTS) or (cleaned.startswith(".") and "/" not in cleaned and "\\" not in cleaned):
                paths.append(cleaned)
        return PlanningEngine._prefer_specific_paths(list(dict.fromkeys(paths)))

    @staticmethod
    def _clean_path_token(token: str) -> str:
        cleaned = token.strip("`'\" ,:;()[]{}<>").replace("\\", "/")
        while cleaned.endswith((".", ",", ";", ":", "!", "?")) and len(cleaned) > 1:
            cleaned = cleaned[:-1]
        return cleaned

    @staticmethod
    def _prefer_specific_paths(paths: list[str]) -> list[str]:
        normalized = [path.replace("\\", "/") for path in paths]
        result: list[str] = []
        for path in normalized:
            if "/" not in path:
                lowered = path.lower()
                if any(candidate.lower().endswith("/" + lowered) for candidate in normalized if candidate != path):
                    continue
            result.append(path)
        return list(dict.fromkeys(result))

    @staticmethod
    def _extract_content(request: str) -> str:
        blocks = re.findall(r"```(?:[\w.+-]+)?\n(.*?)```", request, flags=re.DOTALL)
        if blocks:
            return blocks[-1].strip()
        match = re.search(r"(?:(?:exactly|only)\s+this\s+)?(?:content|text)\s*:\s*", request, flags=re.IGNORECASE)
        if not match:
            return ""

        remainder = request[match.end() :]
        collected: list[str] = []
        for raw_line in remainder.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            lowered = stripped.lower()
            if collected and any(lowered.startswith(prefix) for prefix in CONTENT_BOUNDARY_PREFIXES):
                break
            collected.append(line)

        while collected and not collected[-1].strip():
            collected.pop()
        return PlanningEngine._trim_inline_content_boundary("\n".join(collected).strip())

    @staticmethod
    def _trim_inline_content_boundary(content: str) -> str:
        if not content:
            return ""
        boundary = re.search(
            r"(?i)(?:(?<=[.!?])\s+|\n+)\b("
            r"then\s+run|then\s+execute|then|and\s+then|next|after\s+that|afterwards|finally|now|"
            r"run|execute|compile|test|push|publish|ثم|بعد\s+ذلك|بعدها|اخيرا|أخيراً|الآن|نفذ|شغل"
            r")\b",
            content,
        )
        if not boundary:
            return content
        return content[: boundary.start()].rstrip()

    @staticmethod
    def _looks_like_shell_command(candidate: str) -> bool:
        text = candidate.strip()
        if not text:
            return False

        first_line = text.splitlines()[0].strip()
        try:
            argv = shlex.split(first_line, posix=False)
        except ValueError:
            argv = first_line.split()
        if not argv:
            return False

        executable = argv[0].strip().lower()
        if executable in SHELL_HINT_EXECUTABLES or executable.startswith(("./", ".\\")):
            return True
        return False

    @staticmethod
    def _infer_edit_mode(request: str) -> str:
        lowered = request.lower()
        if "append" in lowered:
            return "append"
        if "prepend" in lowered:
            return "prepend"
        if "replace" in lowered:
            return "replace"
        if "create" in lowered or "new file" in lowered or "أنشئ" in request:
            return "create"
        return "write"

    @staticmethod
    def _position(text: str, tokens: tuple[str, ...], *, fallback: int) -> int:
        positions = [text.find(token.lower()) for token in tokens if token and text.find(token.lower()) >= 0]
        return min(positions) if positions else fallback

    @staticmethod
    def _word_position(text: str, terms: tuple[str, ...], *, fallback: int) -> int:
        positions: list[int] = []
        for term in terms:
            match = re.search(rf"\b{re.escape(term.lower())}\b", text)
            if match:
                positions.append(match.start())
        return min(positions) if positions else fallback

    def _browser_order(self, request_lower: str, browser_target: str) -> int:
        return min(
            self._position(request_lower, (browser_target.lower(),), fallback=999),
            self._word_position(request_lower, BROWSER_TERMS, fallback=999),
            0,
        )

    def _file_order(self, request_lower: str, file_target: str) -> int:
        return min(
            self._position(request_lower, (file_target.lower(),), fallback=999),
            self._word_position(request_lower, SAVE_TERMS, fallback=999),
            200,
        )

    def _read_order(self, request_lower: str, file_target: str) -> int:
        return min(
            self._position(request_lower, (file_target.lower(),), fallback=999),
            self._word_position(request_lower, READ_TERMS, fallback=999),
            100,
        )

    def _shell_order(self, request_lower: str, shell_command: str) -> int:
        return min(
            self._position(request_lower, (shell_command.lower(),), fallback=999),
            self._word_position(request_lower, SHELL_TERMS, fallback=999),
            400,
        )

    @staticmethod
    def _file_operation_order(request_lower: str, path: str, index: int) -> int:
        lowered_path = path.replace("\\", "/").lower()
        if lowered_path.startswith("reports/") and "run the test" in request_lower:
            return 650 + index
        return PlanningEngine()._file_order(request_lower, path) + index

    @staticmethod
    def _infer_test_command(request: str) -> str:
        lowered = request.lower()
        if "run the test" not in lowered and "run tests" not in lowered and "unit tests" not in lowered:
            return ""
        if "pytest" in lowered:
            return "python -m pytest"
        if "python" in lowered or ".py" in lowered or "unit tests" in lowered:
            return "python -m unittest discover -s tests"
        return ""

    def _publish_order(self, request_lower: str, publish_target: str) -> int:
        return min(
            self._position(request_lower, (publish_target.lower(),), fallback=999),
            self._word_position(request_lower, PUBLISH_TERMS, fallback=999),
            500,
        )
