from __future__ import annotations

from collections import Counter
import difflib
import fnmatch
from pathlib import Path
import re

from forge.config.settings import OperatorSettings


IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "then",
    "give",
    "need",
    "want",
    "show",
    "read",
    "file",
    "path",
    "code",
    "repo",
    "project",
    "analyze",
    "inspect",
    "explain",
    "summary",
    "save",
    "export",
    "حول",
    "هذا",
    "هذه",
    "ملف",
    "مشروع",
    "افحص",
    "حلل",
    "اشرح",
    "احفظ",
    "صدر",
}

TEXT_EXTENSIONS = {
    ".cs",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

MAX_SCAN_FILE_BYTES = 300_000


class WorkspaceTools:
    """Safe local workspace inspection and artifact writing."""

    def __init__(self, settings: OperatorSettings) -> None:
        self.settings = settings
        self.workspace_root = settings.workspace_root.resolve()
        self.artifact_root = settings.artifact_root.resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def tree_snapshot(self, max_depth: int = 3, limit: int = 120) -> list[str]:
        lines: list[str] = []
        for path in sorted(self.workspace_root.rglob("*")):
            if len(lines) >= limit:
                break
            if self._should_skip(path):
                continue
            relative = path.relative_to(self.workspace_root)
            depth = len(relative.parts)
            if depth > max_depth:
                continue
            suffix = "/" if path.is_dir() else ""
            lines.append(f"{relative.as_posix()}{suffix}")
        return lines

    def key_files(self) -> list[str]:
        patterns = [
            "README.md",
            "pyproject.toml",
            "package.json",
            "requirements.txt",
            "*.json",
            "*.sln",
            "*.csproj",
            "*.tsx",
            "*.ts",
            "*.py",
        ]
        found: list[str] = []
        for path in sorted(self.workspace_root.rglob("*")):
            if self._should_skip(path) or not path.is_file():
                continue
            relative = path.relative_to(self.workspace_root).as_posix()
            if any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
                found.append(relative)
            if len(found) >= 40:
                break
        return found

    def read_text(self, relative_path: str, max_chars: int = 4000) -> str:
        path = self._resolve_inside_workspace(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

    def read_full_text(self, relative_path: str, max_chars: int = 120_000) -> str:
        path = self._resolve_inside_workspace(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

    def read_excerpt(
        self,
        relative_path: str,
        start_line: int = 1,
        end_line: int = 160,
        max_chars: int = 6000,
    ) -> dict:
        path = self._resolve_inside_workspace(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = max(1, start_line)
        stop = max(start, min(end_line, len(lines)))
        excerpt = "\n".join(lines[start - 1:stop])[:max_chars]
        return {
            "path": path.relative_to(self.workspace_root).as_posix(),
            "start_line": start,
            "end_line": stop,
            "content": excerpt,
        }

    def search_text(self, query: str, max_hits: int = 20) -> list[dict]:
        tokens = self._query_tokens(query)
        if not tokens:
            return []

        hits: list[dict] = []
        for path in sorted(self.workspace_root.rglob("*")):
            if self._should_skip(path) or not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            if path.stat().st_size > MAX_SCAN_FILE_BYTES:
                continue

            relative = path.relative_to(self.workspace_root).as_posix()
            path_text = relative.lower()
            path_boost = sum(1 for token in tokens if token in path_text)
            content = path.read_text(encoding="utf-8", errors="ignore")
            if not content:
                continue

            for line_number, raw_line in enumerate(content.splitlines(), start=1):
                normalized = raw_line.lower()
                token_hits = sum(normalized.count(token) for token in tokens)
                if token_hits <= 0 and path_boost <= 0:
                    continue

                score = token_hits + (path_boost * 2)
                snippet = " ".join(raw_line.strip().split())
                if not snippet:
                    continue
                hits.append(
                    {
                        "path": relative,
                        "line": line_number,
                        "text": snippet[:220],
                        "score": score,
                    }
                )

        hits.sort(key=lambda item: (-item["score"], item["path"], item["line"]))
        return hits[:max_hits]

    def write_artifact(self, relative_path: str, content: str, overwrite: bool = False) -> Path:
        target = (self.artifact_root / relative_path).resolve()
        if self.artifact_root not in target.parents and target != self.artifact_root:
            raise PermissionError("Artifact target escapes the allowed artifact root.")
        if target.exists() and not overwrite:
            raise FileExistsError(f"Artifact already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def preview_text_edit(
        self,
        relative_path: str,
        mode: str,
        content: str | None = None,
        find_text: str | None = None,
        replace_text: str | None = None,
    ) -> dict:
        target = self._resolve_inside_workspace(relative_path)
        existed = target.exists()
        if existed and not target.is_file():
            raise IsADirectoryError(relative_path)

        before = target.read_text(encoding="utf-8", errors="ignore") if existed else ""
        after = self._render_text_edit(
            before=before,
            existed=existed,
            mode=mode,
            content=content,
            find_text=find_text,
            replace_text=replace_text,
        )
        after = self._preserve_line_endings(before, after)
        diff = self._unified_diff(relative_path, before, after)
        return {
            "path": target.relative_to(self.workspace_root).as_posix(),
            "mode": mode,
            "created": not existed,
            "existed_before": existed,
            "before": before,
            "changed": before != after,
            "bytes_before": len(before.encode("utf-8")),
            "bytes_after": len(after.encode("utf-8")),
            "diff": diff,
            "after": after,
        }

    def apply_text_edit(
        self,
        relative_path: str,
        mode: str,
        content: str | None = None,
        find_text: str | None = None,
        replace_text: str | None = None,
    ) -> dict:
        preview = self.preview_text_edit(
            relative_path=relative_path,
            mode=mode,
            content=content,
            find_text=find_text,
            replace_text=replace_text,
        )
        target = self._resolve_inside_workspace(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(preview["after"], encoding="utf-8", newline="")
        return {
            "path": preview["path"],
            "mode": preview["mode"],
            "created": preview["created"],
            "changed": preview["changed"],
            "bytes_before": preview["bytes_before"],
            "bytes_after": target.stat().st_size,
            "bytes_written": target.stat().st_size,
            "diff": preview["diff"],
            "rollback": {
                "path": preview["path"],
                "existed_before": preview["existed_before"],
                "previous_content": preview["before"],
            },
        }

    def rollback_text_edit(
        self,
        relative_path: str,
        *,
        existed_before: bool,
        previous_content: str,
    ) -> dict:
        target = self._resolve_inside_workspace(relative_path)
        if existed_before:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(previous_content, encoding="utf-8", newline="")
            return {
                "path": target.relative_to(self.workspace_root).as_posix(),
                "restored": True,
                "deleted": False,
            }

        if target.exists():
            target.unlink()
        return {
            "path": target.relative_to(self.workspace_root).as_posix(),
            "restored": False,
            "deleted": True,
        }

    def workspace_summary(self) -> dict:
        files = [path for path in self.workspace_root.rglob("*") if path.is_file() and not self._should_skip(path)]
        extension_counts = Counter(path.suffix.lower() or "<none>" for path in files)
        return {
            "workspace_root": str(self.workspace_root),
            "artifact_root": str(self.artifact_root),
            "file_count": len(files),
            "tree": self.tree_snapshot(),
            "key_files": self.key_files(),
            "file_types": dict(extension_counts.most_common(8)),
        }

    def _resolve_inside_workspace(self, relative_path: str) -> Path:
        target = (self.workspace_root / relative_path).resolve()
        if self.workspace_root not in target.parents and target != self.workspace_root:
            raise PermissionError("Path escapes workspace root.")
        return target

    def resolve_workspace_path(self, relative_path: str) -> Path:
        return self._resolve_inside_workspace(relative_path)

    def _should_skip(self, path: Path) -> bool:
        if path == self.artifact_root or self.artifact_root in path.parents:
            return True
        return any(part in IGNORED_DIRS for part in path.parts)

    @staticmethod
    def _render_text_edit(
        before: str,
        existed: bool,
        mode: str,
        content: str | None,
        find_text: str | None,
        replace_text: str | None,
    ) -> str:
        normalized_mode = mode.lower().strip()

        if normalized_mode == "create":
            if existed:
                raise FileExistsError("Create mode refused to overwrite an existing file.")
            if content is None:
                raise ValueError("Create mode requires content.")
            return content

        if normalized_mode in {"write", "overwrite"}:
            if content is None:
                raise ValueError("Write mode requires content.")
            return content

        if normalized_mode == "append":
            if content is None:
                raise ValueError("Append mode requires content.")
            separator = ""
            if before and not before.endswith(("\n", "\r")):
                separator = "\n"
            return before + separator + content

        if normalized_mode == "prepend":
            if content is None:
                raise ValueError("Prepend mode requires content.")
            separator = ""
            if before and not content.endswith(("\n", "\r")):
                separator = "\n"
            return content + separator + before

        if normalized_mode == "replace":
            if not existed:
                raise FileNotFoundError("Replace mode requires an existing file.")
            if not find_text:
                raise ValueError("Replace mode requires find_text.")
            if replace_text is None:
                raise ValueError("Replace mode requires replace_text.")
            if find_text not in before:
                raise ValueError("The requested text to replace was not found in the target file.")
            return before.replace(find_text, replace_text, 1)

        raise ValueError(f"Unsupported edit mode: {mode}")

    @staticmethod
    def _preserve_line_endings(before: str, after: str) -> str:
        if "\r\n" in before and "\r\n" not in after:
            return after.replace("\r\n", "\n").replace("\n", "\r\n")
        return after

    @staticmethod
    def _unified_diff(relative_path: str, before: str, after: str) -> str:
        if before == after:
            return ""
        diff_lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
        return "".join(diff_lines)[:20_000]

    @staticmethod
    def _query_tokens(text: str) -> list[str]:
        tokens = []
        for token in re.findall(r"[\w./\\-]+", text.lower(), flags=re.UNICODE):
            cleaned = token.replace("\\", "/").strip("./-")
            if len(cleaned) < 3:
                continue
            if cleaned in STOP_WORDS:
                continue
            tokens.append(cleaned)
        return list(dict.fromkeys(tokens))
