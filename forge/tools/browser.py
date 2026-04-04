from __future__ import annotations

import asyncio
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import aiohttp

from forge.config.settings import OperatorSettings


BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)

INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "radio",
    "searchbox",
    "switch",
    "tab",
    "textbox",
}
INPUT_ROLES = {"combobox", "searchbox", "spinbutton", "textbox"}
TEXT_ROLES = {"statictext", "labeltext"}


class BrowserActionError(RuntimeError):
    pass


class ChromiumSemanticBrowser:
    """Chromium CDP controller with isolated profile and semantic page snapshots."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._browser_path = self._resolve_browser_path()
        self._port: int | None = None
        self._profile_dir: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._socket: aiohttp.ClientWebSocketResponse | None = None
        self._message_id = 0
        self._snapshot_refs: dict[str, int] = {}

    def run_actions(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return asyncio.run(self._run(actions))

    async def _run(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        if not actions:
            raise ValueError("browser-executor requires at least one browser action.")

        await self._start()
        action_results: list[dict[str, Any]] = []
        try:
            for index, action in enumerate(actions, start=1):
                result = await self._perform_action(index, action)
                action_results.append(result)

            snapshot = await self.semantic_snapshot()
            current_url = await self._evaluate_string("location.href")
            title = await self._evaluate_string("document.title")
            action_trace = self._format_action_trace(action_results)
            summary = (
                f"Browser mission completed on `{current_url}` with {len(action_results)} action(s). "
                f"Captured {snapshot['interactive_count']} interactive node(s) from the accessibility tree."
            )
            evidence = [
                f"url:{current_url}",
                f"title:{title}",
                f"interactive_count:{snapshot['interactive_count']}",
            ]
            return {
                "status": "completed",
                "summary": summary,
                "current_url": current_url,
                "title": title,
                "session_isolated": True,
                "browser_path": str(self._browser_path),
                "action_results": action_results,
                "action_trace": action_trace,
                "page_state": snapshot["page_state"],
                "snapshot_text": snapshot["snapshot_text"],
                "evidence": evidence,
            }
        finally:
            await self._shutdown()

    async def _perform_action(self, index: int, action: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action.get("type", "")).strip().lower()
        if action_type == "navigate":
            url = str(action.get("url", "")).strip()
            if not url:
                raise ValueError("Navigate action requires a URL.")
            await self._navigate(url)
            return {
                "index": index,
                "type": action_type,
                "status": "completed",
                "url": await self._evaluate_string("location.href"),
                "title": await self._evaluate_string("document.title"),
            }

        if action_type == "click":
            object_id, metadata = await self._resolve_target(action.get("target"), mode=action_type)
            result = await self._call_on_object(
                object_id,
                "function() { this.scrollIntoView({block: 'center', inline: 'center'}); this.focus(); this.click(); return {tag:this.tagName, text:(this.innerText||this.textContent||this.value||'').trim()}; }",
            )
            await asyncio.sleep(0.35)
            await self._wait_until_ready()
            return {
                "index": index,
                "type": action_type,
                "status": "completed",
                "target": metadata,
                "result": result,
            }

        if action_type == "fill":
            object_id, metadata = await self._resolve_target(action.get("target"), mode=action_type)
            value = str(action.get("value", ""))
            result = await self._call_on_object(
                object_id,
                "function(value) { this.scrollIntoView({block: 'center', inline: 'center'}); this.focus(); if ('value' in this) { this.value = value; } else if (this.isContentEditable) { this.textContent = value; } this.dispatchEvent(new Event('input', {bubbles:true})); this.dispatchEvent(new Event('change', {bubbles:true})); return {tag:this.tagName, value:('value' in this ? this.value : this.textContent || ''), id:this.id || ''}; }",
                arguments=[{"value": value}],
            )
            return {
                "index": index,
                "type": action_type,
                "status": "completed",
                "target": metadata,
                "result": result,
            }

        if action_type == "extract":
            object_id, metadata = await self._resolve_target(action.get("target"), mode=action_type)
            result = await self._call_on_object(
                object_id,
                "function() { const text = (this.innerText || this.textContent || this.value || '').trim(); return {text:text.slice(0,4000), html:(this.outerHTML || '').slice(0,2000), tag:this.tagName}; }",
            )
            return {
                "index": index,
                "type": action_type,
                "status": "completed",
                "target": metadata,
                "result": result,
            }

        if action_type == "snapshot":
            snapshot = await self.semantic_snapshot()
            return {
                "index": index,
                "type": action_type,
                "status": "completed",
                "interactive_count": snapshot["interactive_count"],
                "title": await self._evaluate_string("document.title"),
            }

        raise ValueError(f"Unsupported browser action: {action_type}")

    async def semantic_snapshot(self) -> dict[str, Any]:
        ax_tree = await self._send("Accessibility.getFullAXTree")
        title = await self._evaluate_string("document.title")
        current_url = await self._evaluate_string("location.href")

        page_state = {
            "headings": [],
            "buttons": [],
            "inputs": [],
            "links": [],
            "text": [],
        }
        self._snapshot_refs.clear()
        ref_counter = 0
        seen: set[tuple[str, str, str]] = set()

        for node in ax_tree.get("nodes", []):
            if node.get("ignored"):
                continue
            role = str((node.get("role") or {}).get("value") or "").strip()
            normalized_role = role.lower()
            if not normalized_role or normalized_role in {"rootwebarea", "none", "generic"}:
                continue

            name = self._ax_string(node.get("name"))
            value = self._ax_string(node.get("value"))
            description = self._ax_string(node.get("description"))
            if normalized_role == "inlinetextbox":
                continue

            if normalized_role == "heading":
                entry = self._semantic_entry(role, name or value, value, description, node)
                if entry and self._dedupe_add(page_state["headings"], entry, seen):
                    continue
            elif normalized_role in INTERACTIVE_ROLES:
                ref_counter += 1
                ref = f"ax-{ref_counter}"
                backend_node_id = node.get("backendDOMNodeId")
                if backend_node_id:
                    self._snapshot_refs[ref] = int(backend_node_id)
                entry = self._semantic_entry(role, name, value, description, node, ref=ref)
                if entry:
                    bucket = "inputs" if normalized_role in INPUT_ROLES else "links" if normalized_role == "link" else "buttons"
                    self._dedupe_add(page_state[bucket], entry, seen)
            elif normalized_role in TEXT_ROLES and (name or value):
                entry = self._semantic_entry(role, name, value, description, node)
                if entry:
                    self._dedupe_add(page_state["text"], entry, seen)

        for key in page_state:
            limit = self._settings.browser_text_limit if key == "text" else self._settings.browser_snapshot_limit
            page_state[key] = page_state[key][:limit]

        snapshot_text = self._format_snapshot_text(title, current_url, page_state)
        interactive_count = sum(len(page_state[key]) for key in ("buttons", "inputs", "links"))
        return {
            "title": title,
            "current_url": current_url,
            "interactive_count": interactive_count,
            "page_state": page_state,
            "snapshot_text": snapshot_text,
        }

    async def _start(self) -> None:
        self._port = self._reserve_port()
        self._profile_dir = tempfile.mkdtemp(prefix="forge-browser-")
        args = [
            str(self._browser_path),
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-popup-blocking",
            "--disable-notifications",
            "--disable-breakpad",
            "--disable-features=AutofillServerCommunication,OptimizationHints",
            "about:blank",
        ]
        if self._settings.browser_headless:
            args.insert(3, "--headless=new")
        self._process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._session = aiohttp.ClientSession()
        target = await self._wait_for_page_target()
        self._socket = await self._session.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0)
        await self._send("Page.enable")
        await self._send("Runtime.enable")
        await self._send("DOM.enable")
        await self._send("Accessibility.enable")

    async def _shutdown(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
        if self._session is not None:
            await self._session.close()
            self._session = None

        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self._process.pid)],
                    capture_output=True,
                    check=False,
                    text=True,
                )
        self._process = None

        if self._profile_dir:
            time.sleep(0.2)
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None

    async def _wait_for_page_target(self) -> dict[str, Any]:
        deadline = time.monotonic() + self._settings.browser_timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{self._port}/json/list", timeout=1) as response:
                    targets = __import__("json").load(response)
                for target in targets:
                    if target.get("type") == "page":
                        return target
            except URLError as exc:
                last_error = str(exc)
            except Exception as exc:  # pragma: no cover - probe error path
                last_error = str(exc)
            await asyncio.sleep(0.2)
        raise BrowserActionError(f"Unable to connect to Chromium CDP page target. {last_error}")

    async def _navigate(self, url: str) -> None:
        result = await self._send("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise BrowserActionError(result["errorText"])
        await self._wait_until_ready()

    async def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self._settings.browser_timeout_seconds
        while time.monotonic() < deadline:
            href = await self._evaluate_string("location.href")
            state = await self._evaluate_string("document.readyState")
            if href and href != "about:blank" and state == "complete":
                return
            await asyncio.sleep(0.25)
        raise TimeoutError("Browser page did not reach ready state before timeout.")

    async def _resolve_target(self, target: Any, *, mode: str = "") -> tuple[str, dict[str, Any]]:
        if not target:
            raise ValueError("Browser action requires a target.")
        if isinstance(target, str):
            target = {"text": target}

        if not isinstance(target, dict):
            raise ValueError("Browser target must be a string or object.")

        ref = str(target.get("ref") or "").strip()
        if ref:
            backend_node_id = self._snapshot_refs.get(ref)
            if backend_node_id:
                resolved = await self._send("DOM.resolveNode", {"backendNodeId": backend_node_id})
                object_id = (resolved.get("object") or {}).get("objectId")
                if object_id:
                    return object_id, {"ref": ref, "backend_node_id": backend_node_id}

        expression = self._target_expression(target, mode=mode)
        result = await self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": False,
                "objectGroup": "forge-browser",
            },
        )
        remote_object = result.get("result", {})
        object_id = remote_object.get("objectId")
        if not object_id or remote_object.get("subtype") == "null":
            raise BrowserActionError(f"Unable to resolve browser target: {target}")
        return object_id, {
            "selector": target.get("selector"),
            "text": target.get("text"),
            "role": target.get("role"),
        }

    async def _evaluate_string(self, expression: str) -> str:
        result = await self._send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        value = (result.get("result") or {}).get("value")
        return "" if value is None else str(value)

    async def _call_on_object(
        self,
        object_id: str,
        function_declaration: str,
        arguments: list[dict[str, Any]] | None = None,
    ) -> Any:
        result = await self._send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": function_declaration,
                "arguments": arguments or [],
                "returnByValue": True,
            },
        )
        remote = result.get("result", {})
        return remote.get("value")

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._socket is None:
            raise BrowserActionError("CDP socket is not connected.")

        self._message_id += 1
        message_id = self._message_id
        await self._socket.send_json({"id": message_id, "method": method, "params": params or {}})

        while True:
            message = await self._socket.receive_json()
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise BrowserActionError(f"{method} failed: {message['error']}")
            return message.get("result", {})

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _resolve_browser_path() -> Path:
        for candidate in BROWSER_CANDIDATES:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("No Chromium-compatible browser was found on this machine.")

    @staticmethod
    def _ax_string(value: dict[str, Any] | None) -> str:
        if not value:
            return ""
        raw = value.get("value")
        return "" if raw is None else str(raw).strip()

    @staticmethod
    def _semantic_entry(
        role: str,
        name: str,
        value: str,
        description: str,
        node: dict[str, Any],
        *,
        ref: str = "",
    ) -> dict[str, Any] | None:
        if not any((name, value, description)):
            return None
        entry = {
            "role": role,
            "name": name,
            "value": value,
            "description": description,
        }
        if ref:
            entry["ref"] = ref
        if node.get("backendDOMNodeId"):
            entry["backend_node_id"] = node["backendDOMNodeId"]
        return entry

    @staticmethod
    def _dedupe_add(bucket: list[dict[str, Any]], entry: dict[str, Any], seen: set[tuple[str, str, str]]) -> bool:
        signature = (
            str(entry.get("role", "")).lower(),
            str(entry.get("name", "")).strip(),
            str(entry.get("value", "")).strip(),
        )
        if signature in seen:
            return False
        seen.add(signature)
        bucket.append(entry)
        return True

    @staticmethod
    def _target_expression(target: dict[str, Any], *, mode: str = "") -> str:
        import json

        payload = json.dumps({**target, "_mode": mode})
        return f"""
(() => {{
  const target = {payload};
  const normalize = (value) => (value || '').trim().toLowerCase();
  const selector = normalize(target.selector);
  const text = normalize(target.text);
  const role = normalize(target.role);
  const mode = normalize(target._mode);
  const candidates = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],label,[contenteditable=\"true\"]'));
  const allElements = Array.from(document.querySelectorAll('body *'));
  const fillable = Array.from(document.querySelectorAll('input,textarea,select,[contenteditable=\"true\"]'));

  if (selector) {{
    const selected = document.querySelector(target.selector);
    if (selected) return selected;
  }}

  const filtered = role
    ? candidates.filter((element) => normalize(element.getAttribute('role') || element.tagName).includes(role))
    : candidates;

  if (mode === 'fill' && text) {{
    const labeled = Array.from(document.querySelectorAll('label')).find((label) => normalize(label.innerText || label.textContent || '').includes(text));
    if (labeled) {{
      if (labeled.control) return labeled.control;
      const forId = labeled.getAttribute('for');
      if (forId) {{
        const control = document.getElementById(forId);
        if (control) return control;
      }}
    }}
    const fillTarget = fillable.find((element) => {{
      const pieces = [
        element.value,
        element.getAttribute('aria-label'),
        element.getAttribute('placeholder'),
        element.name,
        element.id,
      ].filter(Boolean).join(' ');
      return normalize(pieces).includes(text);
    }});
    if (fillTarget) return fillTarget;
  }}

  if (text) {{
    const fromInteractive = filtered.find((element) => {{
      const pieces = [
        element.innerText,
        element.textContent,
        element.value,
        element.getAttribute('aria-label'),
        element.getAttribute('placeholder'),
        element.name,
        element.id,
      ].filter(Boolean).join(' ');
      return normalize(pieces).includes(text);
    }});
    if (fromInteractive) return fromInteractive;
    const matchingElements = allElements.filter((element) => normalize(element.innerText || element.textContent || '').includes(text));
    if (mode === 'extract') {{
      const exact = matchingElements.find((element) => normalize(element.innerText || element.textContent || '') === text);
      if (exact) return exact;
      matchingElements.sort((left, right) => (left.innerText || left.textContent || '').length - (right.innerText || right.textContent || '').length);
    }}
    return matchingElements[0] || null;
  }}

  return filtered[0] || null;
}})()
"""

    @staticmethod
    def _format_snapshot_text(title: str, current_url: str, page_state: dict[str, list[dict[str, Any]]]) -> str:
        lines = [
            "# Browser Snapshot",
            f"- Title: {title or '(untitled)'}",
            f"- URL: {current_url}",
            "",
            "## Headings",
            *ChromiumSemanticBrowser._bucket_lines(page_state.get("headings", [])),
            "",
            "## Inputs",
            *ChromiumSemanticBrowser._bucket_lines(page_state.get("inputs", [])),
            "",
            "## Buttons",
            *ChromiumSemanticBrowser._bucket_lines(page_state.get("buttons", [])),
            "",
            "## Links",
            *ChromiumSemanticBrowser._bucket_lines(page_state.get("links", [])),
            "",
            "## Text",
            *ChromiumSemanticBrowser._bucket_lines(page_state.get("text", [])),
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _format_action_trace(action_results: list[dict[str, Any]]) -> str:
        if not action_results:
            return ""
        lines = ["# Browser Actions"]
        for action in action_results:
            action_type = action.get("type", "unknown")
            if action_type == "navigate":
                lines.append(f"- navigate -> {action.get('url', '')} | title={action.get('title', '')}")
            elif action_type in {"click", "fill", "extract"}:
                lines.append(f"- {action_type} -> target={action.get('target', {})} | result={action.get('result', {})}")
            elif action_type == "snapshot":
                lines.append(f"- snapshot -> interactive_count={action.get('interactive_count', 0)}")
            else:
                lines.append(f"- {action_type}")
        return "\n".join(lines)

    @staticmethod
    def _bucket_lines(entries: list[dict[str, Any]]) -> list[str]:
        if not entries:
            return ["- none"]
        lines: list[str] = []
        for entry in entries:
            ref = f"[{entry['ref']}] " if entry.get("ref") else ""
            primary = entry.get("name") or entry.get("value") or entry.get("description") or "(unnamed)"
            value = f" | value={entry['value']}" if entry.get("value") and entry.get("value") != primary else ""
            role = entry.get("role")
            lines.append(f"- {ref}{primary} ({role}){value}")
        return lines


URL_PATTERN = re.compile(r"(https?://[^\s`\"']+|file://[^\s`\"']+|data:text/html,[^\s`\"']+)", flags=re.IGNORECASE)


def build_file_url(path: Path) -> str:
    return path.resolve().as_uri()
