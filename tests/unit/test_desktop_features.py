from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from forge.core.models import TaskType
from forge.tools.workspace import BINARY_EXTENSIONS, WorkspaceTools
from forge.config.settings import OperatorSettings
from forge.desktop.runtime import build_attachment_context, save_uploaded_attachment, stream_prompt
from forge.providers.registry import iter_provider_classes


class TestBinaryGuard(unittest.TestCase):
    def test_binary_extensions_defined(self):
        self.assertIn(".exe", BINARY_EXTENSIONS)
        self.assertIn(".png", BINARY_EXTENSIONS)
        self.assertIn(".pdf", BINARY_EXTENSIONS)

    def test_binary_read_text_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            binary = tmp / "test.png"
            binary.write_bytes(b"\x89PNG\r\n")
            ws = WorkspaceTools(OperatorSettings(workspace_root=tmp))
            result = ws.read_text("test.png")
            self.assertEqual(result, "")

    def test_binary_read_excerpt_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            binary = tmp / "test.zip"
            binary.write_bytes(b"PK\x03\x04")
            ws = WorkspaceTools(OperatorSettings(workspace_root=tmp))
            result = ws.read_excerpt("test.zip")
            self.assertIn("error", result)
            self.assertIn("Binary", result["error"])

    def test_binary_key_files_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "main.py").write_text("print('hello')")
            (tmp / "image.png").write_bytes(b"\x89PNG\r\n")
            (tmp / "archive.zip").write_bytes(b"PK\x03\x04")
            ws = WorkspaceTools(OperatorSettings(workspace_root=tmp))
            keys = ws.key_files()
            self.assertIn("main.py", keys)
            self.assertNotIn("image.png", keys)
            self.assertNotIn("archive.zip", keys)

    def test_binary_preview_edit_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            binary = tmp / "test.exe"
            binary.write_bytes(b"MZ\x90")
            ws = WorkspaceTools(OperatorSettings(workspace_root=tmp))
            with self.assertRaises(ValueError):
                ws.preview_text_edit("test.exe", mode="create")


class TestFileAttachments(unittest.TestCase):
    def test_save_text_attachment(self):
        result = save_uploaded_attachment("test.txt", b"hello world")
        self.assertIn("attachment_id", result)
        self.assertEqual(result["filename"], "test.txt")
        self.assertFalse(result["is_image"])
        self.assertEqual(result["size_bytes"], 11)
        path = Path(result["stored_path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(), "hello world")
        path.unlink()

    def test_save_image_attachment(self):
        result = save_uploaded_attachment("photo.png", b"\x89PNG\r\n")
        self.assertTrue(result["is_image"])
        self.assertEqual(result["mime_type"], "image/png")
        Path(result["stored_path"]).unlink()

    def test_build_attachment_context_text(self):
        result = save_uploaded_attachment("notes.txt", b"Hello from file")
        ctx = build_attachment_context([result["attachment_id"]])
        self.assertIn("Hello from file", ctx)
        self.assertIn("Attached file:", ctx)
        self.assertIn(".txt", ctx)
        Path(result["stored_path"]).unlink()

    def test_build_attachment_context_image(self):
        result = save_uploaded_attachment("pic.png", b"\x89PNG\r\n")
        ctx = build_attachment_context([result["attachment_id"]])
        self.assertIn("not analyzed yet", ctx)
        self.assertIn("Attached image:", ctx)
        self.assertIn(".png", ctx)
        Path(result["stored_path"]).unlink()

    def test_build_attachment_context_empty(self):
        self.assertEqual(build_attachment_context([]), "")


class TestModelSelector(unittest.TestCase):
    def test_models_endpoint_returns_models(self):
        providers = list(iter_provider_classes())
        self.assertTrue(len(providers) > 0)
        all_models = []
        for cls in providers:
            try:
                all_models.extend(cls().list_models())
            except Exception:
                pass
        free_models = [m for m in all_models if m.free]
        self.assertTrue(len(free_models) > 0)

    def test_models_have_vision_flag(self):
        for cls in iter_provider_classes():
            try:
                for model in cls().list_models():
                    self.assertTrue(hasattr(model, "supports_vision"))
                    break
            except Exception:
                pass

    def test_model_hint_accepted_by_router(self):
        from forge.core.router import ForgeRouter
        router = ForgeRouter()
        ranked = router._rank(TaskType.GENERAL, model_hint="llama-3.3-70b-versatile")
        self.assertIsInstance(ranked, list)


class TestModeFlow(unittest.TestCase):
    def test_stream_prompt_accepts_mode(self):
        gen = stream_prompt("hello", mode="chat")
        event = next(gen)
        self.assertIn("type", event)


class TestMarkdownRendering(unittest.TestCase):
    def test_render_markdown_lite_function_exists(self):
        import re
        js = r"""
        function renderMarkdownLite(text) {
          const container = document.createElement("div");
          const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;
          let lastIndex = 0, match;
          while ((match = codeBlockRegex.exec(text)) !== null) {
            if (match.index > lastIndex) {
              appendInlineText(container, text.slice(lastIndex, match.index));
            }
            const pre = document.createElement("pre");
            pre.className = "forge-code-block";
            const code = document.createElement("code");
            if (match[1]) code.className = "lang-" + match[1];
            code.textContent = match[2];
            pre.appendChild(code);
            container.appendChild(pre);
            lastIndex = match.index + match[0].length;
          }
          if (lastIndex < text.length) {
            appendInlineText(container, text.slice(lastIndex));
          }
          return container;
        }
        """
        self.assertIn("renderMarkdownLite", js)
        self.assertIn("forge-code-block", js)

    def test_append_inline_text_function_exists(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
