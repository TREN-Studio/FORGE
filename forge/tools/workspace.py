from __future__ import annotations

from collections import Counter
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

    def _should_skip(self, path: Path) -> bool:
        if path == self.artifact_root or self.artifact_root in path.parents:
            return True
        return any(part in IGNORED_DIRS for part in path.parts)

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
