from __future__ import annotations

import json
import threading
import webbrowser
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from forge.brain.orchestrator import MissionOrchestrator
from forge import __version__
from forge.config.settings import OperatorSettings
from forge.desktop.account_client import (
    PortalAccountClient,
    PortalApiError,
    SESSION_COOKIE_NAME,
)
from forge.desktop.diagnostics import log_event, log_exception
from forge.desktop.runtime import (
    boot_status,
    boot_status_for_user,
    choose_workspace_root,
    get_workspace_status,
    operate_prompt,
    prepare_demo_workspace,
    run_prompt,
    set_workspace_root,
    stream_prompt,
)
from forge.providers import supported_provider_names


DESKTOP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FORGE Desktop</title>
  <style>
    :root {
      --bg: #060606;
      --panel: rgba(18, 18, 18, 0.86);
      --panel-soft: rgba(26, 26, 26, 0.9);
      --line: rgba(255,255,255,0.08);
      --text: #f4efe8;
      --muted: #a9a096;
      --accent: #ff6b1a;
      --accent-soft: #ffb44d;
      --ok: #67d471;
      --danger: #ff7171;
      --shadow: 0 22px 80px rgba(0,0,0,0.45);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", system-ui, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(255,107,26,0.16), transparent 28%),
        radial-gradient(circle at 80% 12%, rgba(255,180,77,0.12), transparent 24%),
        linear-gradient(135deg, #060606 0%, #0d0d0d 32%, #121212 100%);
    }

    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(transparent 0, rgba(255,255,255,0.015) 50%, transparent 100%),
        repeating-linear-gradient(
          90deg,
          transparent 0,
          transparent 39px,
          rgba(255,255,255,0.018) 40px
        );
      opacity: 0.45;
    }

    body.sidebar-open { overflow: hidden; }

    .shell {
      position: relative;
      min-height: 100vh;
      padding: 0;
    }

    .sidebar-scrim {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.52);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.18s ease;
      z-index: 30;
    }

    body.sidebar-open .sidebar-scrim {
      opacity: 1;
      pointer-events: auto;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .sidebar {
      position: fixed;
      top: 20px;
      left: 20px;
      bottom: 20px;
      width: min(360px, calc(100vw - 40px));
      display: flex;
      flex-direction: column;
      gap: 18px;
      padding: 24px;
      overflow: auto;
      z-index: 40;
      transform: translateX(calc(-100% - 28px));
      transition: transform 0.2s ease;
    }

    body.sidebar-open .sidebar {
      transform: translateX(0);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .brand-mark {
      position: relative;
      width: 62px;
      height: 62px;
      border-radius: 18px;
      background: linear-gradient(160deg, rgba(255,120,44,0.18), rgba(255,255,255,0.04));
      border: 1px solid rgba(255,255,255,0.1);
      overflow: hidden;
      box-shadow: inset 0 0 24px rgba(255,120,44,0.08);
    }

    .brand-mark::before {
      content: "";
      position: absolute;
      left: 18px;
      top: 12px;
      width: 11px;
      height: 38px;
      border-radius: 999px;
      background: linear-gradient(180deg, #ffd08d, #ff6b1a 55%, #7f2300);
      box-shadow: 0 0 22px rgba(255,107,26,0.45);
    }

    .brand-mark::after {
      content: "";
      position: absolute;
      right: -10px;
      bottom: -16px;
      width: 58px;
      height: 38px;
      border-radius: 50%;
      border: 10px solid rgba(255,255,255,0.16);
      transform: rotate(-12deg);
    }

    .brand h1 {
      margin: 0;
      font-size: 31px;
      letter-spacing: 0.08em;
      color: var(--accent);
    }

    .brand p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .card {
      padding: 18px;
      border-radius: 18px;
      background: var(--panel-soft);
      border: 1px solid var(--line);
    }

    .card h2, .card h3 {
      margin: 0 0 10px;
      font-size: 14px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-soft);
    }

    .status-line {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      padding: 12px 0;
      border-bottom: 1px solid rgba(255,255,255,0.05);
    }

    .status-line:last-child { border-bottom: 0; padding-bottom: 0; }

    .status-key {
      color: var(--muted);
      font-size: 13px;
    }

    .status-value {
      color: var(--text);
      font-weight: 600;
      font-size: 14px;
      text-align: right;
      max-width: 62%;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .mode-toggle {
      display: flex;
      align-items: center;
      gap: 12px;
      color: var(--text);
      font-weight: 600;
    }

    .mode-toggle input {
      width: 18px;
      height: 18px;
      accent-color: var(--accent);
    }

    .toggle-stack {
      display: grid;
      gap: 12px;
    }

    .field-label {
      display: block;
      margin: 12px 0 8px;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .text-field {
      width: 100%;
      min-height: 46px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.26);
      color: var(--text);
      padding: 12px 14px;
      font: 14px/1.4 "Segoe UI", sans-serif;
      outline: none;
    }

    .text-field:focus {
      border-color: rgba(255,107,26,0.42);
      box-shadow: 0 0 0 4px rgba(255,107,26,0.08);
    }

    .button-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }

    .button-ghost {
      min-height: 42px;
      background: rgba(255,255,255,0.05);
      color: var(--text);
      border: 1px solid rgba(255,255,255,0.08);
      box-shadow: none;
    }

    .notes {
      min-height: 220px;
      white-space: pre-wrap;
      color: var(--muted);
      font: 13px/1.55 Consolas, monospace;
    }

    .worker-list {
      display: grid;
      gap: 10px;
    }

    .worker-row {
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.025);
    }

    .worker-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
    }

    .worker-name {
      color: var(--text);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
    }

    .worker-state {
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }

    .worker-state.idle { color: var(--muted); }
    .worker-state.busy { color: var(--accent-soft); }
    .worker-state.overloaded { color: #ffd36a; }
    .worker-state.failed { color: var(--danger); }

    .worker-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
    }

    .workspace {
      display: grid;
      grid-template-rows: auto auto 1fr;
      min-height: 100vh;
      width: min(1180px, calc(100vw - 40px));
      margin: 0 auto;
      overflow: hidden;
      background: transparent;
      border: 0;
      box-shadow: none;
      backdrop-filter: none;
    }

    .workspace-header {
      width: min(980px, 100%);
      margin: 0 auto;
      padding: 26px 0 10px;
      border-bottom: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }

    .workspace-header h2 {
      margin: 0;
      font-size: 24px;
      letter-spacing: 0.02em;
    }

    .workspace-header p {
      margin: 6px 0 0;
      max-width: 720px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }

    .auth-gate {
      width: min(720px, 100%);
      margin: 4px auto 0;
      padding: 22px 24px;
      border-radius: 26px;
      border: 1px solid rgba(255,255,255,0.08);
      background: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.02));
      box-shadow: 0 24px 60px rgba(0,0,0,0.22);
    }

    .auth-gate-kicker {
      color: var(--accent-soft);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }

    .auth-gate h3 {
      margin: 8px 0 10px;
      font-size: 31px;
      line-height: 1.05;
      letter-spacing: -0.03em;
    }

    .auth-gate p {
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.7;
    }

    .auth-gate-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }

    .auth-gate-actions button {
      min-height: 48px;
      padding: 0 18px;
      border-radius: 14px;
      font-size: 14px;
    }

    .provider-setup-options {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 18px;
    }

    .provider-setup-option {
      min-height: 92px;
      padding: 14px;
      text-align: left;
    }

    .provider-setup-option strong {
      display: block;
      margin-bottom: 6px;
      font-size: 14px;
    }

    .provider-setup-option span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .provider-setup-status {
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
    }

    .demo-task {
      margin: 20px auto 0;
      max-width: 620px;
      padding: 16px;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 18px;
      background: rgba(255,255,255,0.04);
      text-align: left;
    }

    .demo-task-title {
      margin-bottom: 6px;
      color: var(--text);
      font-size: 14px;
      font-weight: 700;
    }

    .demo-task-copy,
    .demo-task-status {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }

    .demo-task-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 12px;
    }

    .demo-task-actions button {
      min-height: 40px;
      padding: 0 14px;
      border-radius: 12px;
      font-size: 13px;
    }

    .chat-shell {
      width: min(980px, 100%);
      min-height: calc(100vh - 128px);
      margin: 0 auto;
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
    }

    .chat-empty {
      align-self: center;
      justify-self: center;
      width: min(760px, 100%);
      padding: 22px 18px;
      text-align: center;
    }

    .chat-empty-kicker {
      color: var(--accent-soft);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }

    .chat-empty h3 {
      margin: 10px 0 12px;
      font-size: clamp(38px, 7vw, 58px);
      line-height: 0.98;
      letter-spacing: -0.05em;
    }

    .chat-empty p {
      max-width: 680px;
      margin: 0 auto;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.75;
    }

    .workspace-kicker {
      margin-bottom: 6px;
      color: var(--accent-soft);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    .workspace-topbar-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .sidebar-toggle {
      min-height: 42px;
      padding: 0 18px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.05);
      color: var(--text);
      box-shadow: none;
    }

    .operator-deck {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      padding: 20px 28px 0;
    }

    .operator-card {
      padding: 16px 18px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.026);
    }

    .operator-card h3 {
      margin: 0 0 8px;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-soft);
    }

    .operator-value {
      font-size: 22px;
      font-weight: 700;
      line-height: 1.25;
      color: var(--text);
    }

    .operator-copy {
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .operator-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
      gap: 18px;
      padding: 20px 28px 0;
    }

    .operator-deck,
    .operator-grid {
      display: none;
    }

    .operator-panel {
      padding: 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.026);
    }

    .operator-panel h3 {
      margin: 0 0 12px;
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-soft);
    }

    .operator-panel-body {
      color: var(--text);
      white-space: pre-wrap;
      line-height: 1.6;
      font-size: 14px;
      min-height: 80px;
    }

    .operator-list {
      display: grid;
      gap: 10px;
    }

    .operator-item {
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.02);
    }

    .operator-item-title {
      color: var(--text);
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
    }

    .operator-item-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      margin-bottom: 6px;
    }

    .operator-item-body {
      color: var(--text);
      font-size: 14px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.04);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .status-pill.finished { color: var(--ok); border-color: rgba(103,212,113,0.34); background: rgba(103,212,113,0.09); }
    .status-pill.partially_finished { color: #ffd36a; border-color: rgba(255,211,106,0.32); background: rgba(255,211,106,0.09); }
    .status-pill.failed, .status-pill.needs_retry, .status-pill.needs_human_confirmation { color: var(--danger); border-color: rgba(255,113,113,0.34); background: rgba(255,113,113,0.08); }
    .status-pill.running { color: var(--accent-soft); border-color: rgba(255,180,77,0.28); background: rgba(255,180,77,0.08); }

    .chat {
      width: 100%;
      max-width: 100%;
      margin: 0 auto;
      padding: 12px 0 16px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .bubble {
      max-width: min(920px, 100%);
      padding: 15px 16px 14px;
      border-radius: 22px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.028);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.02);
    }

    .bubble.user {
      align-self: flex-end;
      max-width: min(720px, 88%);
      background: linear-gradient(180deg, rgba(255,107,26,0.18), rgba(255,107,26,0.07));
      border-color: rgba(255,107,26,0.28);
    }

    .bubble.assistant {
      align-self: flex-start;
      background: rgba(255,255,255,0.035);
    }

    .bubble.streaming {
      border-color: rgba(255,107,26,0.2);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03), 0 0 0 1px rgba(255,107,26,0.05);
    }

    .bubble.error {
      align-self: flex-start;
      border-color: rgba(255,113,113,0.34);
      background: rgba(255,113,113,0.08);
    }

    .bubble header {
      margin-bottom: 8px;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-soft);
    }

    .bubble.user header { color: #1d1206; font-weight: 800; }
    .bubble .body {
      white-space: pre-wrap;
      line-height: 1.65;
      color: var(--text);
      font-size: 15px;
    }

    .bubble-footer {
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.04em;
    }

    .cursor {
      display: inline-block;
      margin-left: 2px;
      color: var(--accent-soft);
      animation: forge-blink 0.9s steps(1) infinite;
    }

    @keyframes forge-blink {
      0%, 48% { opacity: 1; }
      49%, 100% { opacity: 0; }
    }

    .composer {
      width: 100%;
      max-width: 100%;
      margin: 0 auto;
      padding: 6px 0 24px;
      border-top: 0;
      background: none;
    }

    .composer-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 120px;
      gap: 14px;
      align-items: end;
    }

    textarea {
      width: 100%;
      min-height: 68px;
      max-height: 240px;
      resize: vertical;
      border-radius: 24px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.3);
      color: var(--text);
      padding: 16px 18px;
      font: 15px/1.55 "Segoe UI", sans-serif;
      outline: none;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.02);
    }

    textarea:focus {
      border-color: rgba(255,107,26,0.42);
      box-shadow: 0 0 0 4px rgba(255,107,26,0.1);
    }

    .actions {
      display: grid;
      gap: 12px;
      align-content: start;
    }

    button {
      border: 0;
      border-radius: 16px;
      cursor: pointer;
      transition: transform 0.18s ease, filter 0.18s ease;
      font: 600 15px/1 "Segoe UI", sans-serif;
      min-height: 56px;
    }

    button:hover { transform: translateY(-1px); }
    button:disabled { opacity: 0.6; cursor: wait; transform: none; }

    #send {
      background: linear-gradient(180deg, #ff9d46, #ff6b1a);
      color: #17110c;
      box-shadow: 0 16px 32px rgba(255,107,26,0.24);
    }

    #clear {
      background: rgba(255,255,255,0.06);
      color: var(--text);
      border: 1px solid rgba(255,255,255,0.08);
    }

    #result-panel {
      display: none;
    }

    .diagnostics-toggle {
      justify-self: flex-start;
      min-height: 34px;
      margin-top: 10px;
      padding: 0 12px;
      border-radius: 10px;
      border: 1px solid rgba(255,255,255,0.1);
      background: rgba(255,255,255,0.045);
      color: var(--muted);
      font-size: 12px;
      box-shadow: none;
    }

    .diagnostics-panel {
      max-height: 260px;
      overflow: auto;
      margin: 10px 0 0;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.34);
      color: rgba(247,247,247,0.76);
      font: 12px/1.5 "Consolas", "SFMono-Regular", monospace;
      white-space: pre-wrap;
    }

    .live-progress {
      min-height: 38px;
      margin: 0 0 12px;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.028);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .live-progress.active {
      border-color: rgba(255,107,26,0.18);
      background: rgba(255,107,26,0.045);
    }

    .live-status {
      color: var(--text);
      font-weight: 650;
    }

    .live-steps {
      display: grid;
      gap: 6px;
      margin-top: 8px;
    }

    .live-step {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
    }

    .live-step-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .live-step-state {
      flex: 0 0 auto;
      font-size: 12px;
      color: var(--muted);
    }

    .live-step.running .live-step-state { color: var(--accent-soft); }
    .live-step.done .live-step-state { color: var(--ok); }
    .live-step.failed .live-step-state { color: var(--danger); }

    .footnote {
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .chat-shell.guest-mode .composer {
      opacity: 1;
    }

    .hidden { display: none !important; }

    .auth-grid, .provider-grid {
      display: grid;
      gap: 10px;
    }

    .inline-status {
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
    }

    .secret-list, .admin-list {
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }

    .mini-item {
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.025);
    }

    .mini-title {
      color: var(--text);
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 4px;
    }

    .mini-copy {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
    }

    @media (max-width: 1080px) {
      .workspace {
        min-height: auto;
      }
      .chat-shell {
        min-height: calc(100vh - 110px);
      }
      .composer-grid {
        grid-template-columns: 1fr;
      }
      .workspace-header {
        flex-direction: column;
        align-items: flex-start;
      }
      .auth-gate,
      .chat-shell,
      .workspace-header {
        width: 100%;
      }
      .provider-setup-options {
        grid-template-columns: 1fr;
      }
      .sidebar {
        top: 12px;
        left: 12px;
        bottom: 12px;
        width: min(360px, calc(100vw - 24px));
      }
    }
  </style>
</head>
<body>
  <div id="sidebar-scrim" class="sidebar-scrim"></div>
  <div class="shell">
    <aside class="panel sidebar">
      <div class="brand">
        <div class="brand-mark"></div>
        <div>
          <h1>FORGE</h1>
          <p>Autonomous Operator</p>
        </div>
      </div>

      <section class="card">
        <h2>Account</h2>
        <div id="account-summary" class="footnote">Guest mode is ready. Sign in only for sync, saved cloud keys, admin, or private portal features.</div>
        <div id="auth-logged-out" class="auth-grid">
          <div class="footnote">Optional account path. You can use FORGE locally first, then connect an account when you want cloud key sync or private controls.</div>
          <label class="field-label" for="auth-name">Display Name</label>
          <input id="auth-name" class="text-field" type="text" placeholder="Your name">
          <label class="field-label" for="auth-email">Email</label>
          <input id="auth-email" class="text-field" type="email" placeholder="you@example.com">
          <label class="field-label" for="auth-password">Password</label>
          <input id="auth-password" class="text-field" type="password" placeholder="Minimum 8 characters">
          <div class="button-grid">
            <button id="login-button" type="button" class="button-ghost">Login</button>
            <button id="register-button" type="button" class="button-ghost">Register</button>
          </div>
          <div class="button-grid">
            <button id="google-login-button" type="button" class="button-ghost">Continue with Google</button>
            <button id="browser-setup-button" type="button" class="button-ghost">Complete Setup in Browser</button>
          </div>
        </div>
        <div id="auth-logged-in" class="hidden">
          <div class="status-line">
            <span class="status-key">Signed In</span>
            <span id="account-email" class="status-value">-</span>
          </div>
          <div class="status-line">
            <span class="status-key">Role</span>
            <span id="account-role" class="status-value">User</span>
          </div>
          <div class="status-line">
            <span class="status-key">Manager Gate</span>
            <span id="account-manager-gate" class="status-value">Closed</span>
          </div>
          <div class="status-line">
            <span class="status-key">Email Verification</span>
            <span id="account-email-verification" class="status-value">Pending</span>
          </div>
          <div class="button-grid">
            <button id="logout-button" type="button" class="button-ghost">Logout</button>
            <button id="refresh-account-button" type="button" class="button-ghost">Refresh</button>
          </div>
          <div class="button-grid">
            <button id="send-verification-button" type="button" class="button-ghost">Send Verification</button>
            <button id="request-reset-button" type="button" class="button-ghost">Request Reset</button>
          </div>
        </div>
        <label class="field-label" for="auth-token">Verification / Reset Token</label>
        <input id="auth-token" class="text-field" type="text" placeholder="Paste token from email or portal">
        <label class="field-label" for="auth-new-password">New Password</label>
        <input id="auth-new-password" class="text-field" type="password" placeholder="Use for password reset">
        <div class="button-grid">
          <button id="verify-email-button" type="button" class="button-ghost">Verify Email</button>
          <button id="apply-reset-button" type="button" class="button-ghost">Apply Reset</button>
        </div>
        <div id="auth-status" class="inline-status">Guest mode active. Account sign-in is optional.</div>
      </section>

      <section class="card">
        <h3>My Provider Keys</h3>
        <label class="field-label" for="provider-select">Provider</label>
        <select id="provider-select" class="text-field">
          <option value="cloudflare">cloudflare</option>
          <option value="nvidia">nvidia</option>
          <option value="openai">openai</option>
          <option value="anthropic">anthropic</option>
          <option value="gemini">gemini</option>
          <option value="groq">groq</option>
          <option value="deepseek">deepseek</option>
          <option value="openrouter">openrouter</option>
          <option value="mistral">mistral</option>
          <option value="together">together</option>
        </select>
        <div class="provider-grid">
          <label class="field-label" for="provider-api-key">API Key / Token</label>
          <input id="provider-api-key" class="text-field" type="password" placeholder="API key or token">
          <label class="field-label" for="provider-account-id">Account ID / Extra Field</label>
          <input id="provider-account-id" class="text-field" type="text" placeholder="Cloudflare account_id or leave empty">
          <label class="field-label" for="provider-organization">Organization / Email</label>
          <input id="provider-organization" class="text-field" type="text" placeholder="OpenAI organization or Cloudflare email">
          <label class="field-label" for="provider-project">Project / Global Key</label>
          <input id="provider-project" class="text-field" type="text" placeholder="OpenAI project or Cloudflare global key">
        </div>
        <div class="button-grid">
          <button id="save-provider-key" type="button" class="button-ghost">Save Key</button>
          <button id="refresh-provider-keys" type="button" class="button-ghost">Reload</button>
        </div>
        <div id="provider-status" class="inline-status">Optional: sign in to save encrypted cloud provider keys. The local demo does not need a key.</div>
        <div id="provider-secret-list" class="secret-list">
          <div class="operator-copy">Sign in only when you want saved provider keys.</div>
        </div>
      </section>

      <section class="card">
        <h2>Boot Status</h2>
        <div class="status-line">
          <span class="status-key">Runtime</span>
          <span id="runtime-state" class="status-value">Booting</span>
        </div>
        <div class="status-line">
          <span class="status-key">Providers</span>
          <span id="providers" class="status-value">-</span>
        </div>
        <div class="status-line">
          <span class="status-key">Live Models</span>
          <span id="models" class="status-value">-</span>
        </div>
        <div class="status-line">
          <span class="status-key">Version</span>
          <span id="version" class="status-value">FORGE</span>
        </div>
      </section>

      <section class="card">
        <h3>Execution Mode</h3>
        <label class="mode-toggle">
          <input id="operator-mode" type="checkbox" checked disabled>
          <span>Agent Mode Locked</span>
        </label>
        <p class="footnote">
          FORGE replies like a real assistant by default, then switches into verified execution mode only when your request actually needs tools.
        </p>
      </section>

      <section class="card">
        <h3>Workspace</h3>
        <div class="status-line">
          <span class="status-key">Active Root</span>
          <span id="workspace-name" class="status-value">Loading...</span>
        </div>
        <label class="field-label" for="workspace-path">Project Folder</label>
        <input id="workspace-path" class="text-field" type="text" placeholder="C:\\Projects\\your-repo">
        <div class="button-grid">
          <button id="apply-workspace" type="button" class="button-ghost">Apply Path</button>
          <button id="browse-workspace" type="button" class="button-ghost">Browse...</button>
        </div>
        <p id="workspace-summary" class="footnote">
          Select the real project root. File and shell skills will execute only inside this workspace.
        </p>
      </section>

      <section class="card">
        <h3>Execution Controls</h3>
        <div class="toggle-stack">
          <label class="mode-toggle">
            <input id="confirm-mode" type="checkbox">
            <span>Allow Real Changes</span>
          </label>
          <label class="mode-toggle">
            <input id="dry-run-mode" type="checkbox">
            <span>Force Dry Run</span>
          </label>
        </div>
        <p class="footnote">
          Without confirmation, medium-risk development tasks stay in dry-run. External publishing and high-risk writes still require explicit confirmation.
        </p>
      </section>

      <section class="card">
        <h3>Boot Notes</h3>
        <div id="notes" class="notes">Preparing FORGE runtime...</div>
      </section>

      <section class="card">
        <h3>Worker Telemetry</h3>
        <div id="worker-services" class="worker-list">
          <div class="operator-copy">Worker telemetry loading...</div>
        </div>
      </section>

      <section id="admin-panel" class="card hidden">
        <h3>Admin Control</h3>
        <div id="admin-summary" class="inline-status">Manager view locked.</div>
        <div id="admin-users" class="admin-list">
          <div class="operator-copy">Admin access required.</div>
        </div>
      </section>
    </aside>

    <main class="panel workspace">
      <header class="workspace-header">
        <div>
          <div class="workspace-kicker">FORGE Desktop</div>
          <h2>Talk to FORGE</h2>
          <p id="workspace-subtitle">
            Ask in Arabic or English. FORGE replies naturally, chooses the best response path, and only switches into execution mode when your request actually needs tools.
          </p>
        </div>
        <div class="workspace-topbar-actions">
          <button id="sidebar-toggle" type="button" class="sidebar-toggle">Settings</button>
        </div>
      </header>

      <section id="auth-gate" class="auth-gate hidden">
        <div class="auth-gate-kicker">Optional sync</div>
        <h3>Connect an account only when you need it.</h3>
        <p>
          FORGE Desktop works locally as a guest. Sign in later only if you want saved cloud keys, sync, admin controls, or private portal features.
        </p>
        <div class="auth-gate-actions">
          <button id="auth-gate-google" type="button">Continue with Google</button>
          <button id="auth-gate-account" type="button" class="button-ghost">Email or Settings</button>
        </div>
      </section>

      <section id="provider-setup" class="auth-gate hidden">
        <div class="auth-gate-kicker">First-run provider setup</div>
        <h3>Choose how FORGE should think.</h3>
        <p>
          No working model provider is ready yet. Pick one path now; FORGE will keep the rest of the desktop flow unchanged.
        </p>
        <div class="provider-setup-options">
          <button id="provider-setup-groq" type="button" class="button-ghost provider-setup-option">
            <strong>Groq</strong>
            <span>Fast free-tier cloud path. Add a Groq API key.</span>
          </button>
          <button id="provider-setup-ollama" type="button" class="button-ghost provider-setup-option">
            <strong>Ollama</strong>
            <span>Private local path. Start Ollama and pull a model.</span>
          </button>
          <button id="provider-setup-byok" type="button" class="button-ghost provider-setup-option">
            <strong>BYOK</strong>
            <span>Use OpenAI, Anthropic, NVIDIA, Gemini, or another key.</span>
          </button>
        </div>
        <div id="provider-setup-status" class="provider-setup-status">Checking provider readiness...</div>
      </section>

      <section class="operator-deck">
        <article class="operator-card">
          <h3>Objective</h3>
          <div id="objective" class="operator-value">No active objective.</div>
          <div id="objective-note" class="operator-copy">Submit a serious task to generate a real operator mission.</div>
        </article>
        <article class="operator-card">
          <h3>Validation</h3>
          <div id="validation-state" class="operator-value">Idle</div>
          <div id="validation-note" class="operator-copy">Validation status appears here after execution.</div>
        </article>
        <article class="operator-card">
          <h3>Steps</h3>
          <div id="step-metrics" class="operator-value">0 / 0</div>
          <div id="step-note" class="operator-copy">Completed steps vs total executed steps.</div>
        </article>
        <article class="operator-card">
          <h3>Best Next Action</h3>
          <div id="next-action" class="operator-value">Awaiting mission.</div>
          <div id="next-note" class="operator-copy">FORGE must say what should happen next when a task is partial or blocked.</div>
        </article>
      </section>

      <section class="operator-grid">
        <article class="operator-panel">
          <h3>Execution Plan</h3>
          <div id="plan-list" class="operator-list">
            <div class="operator-copy">No execution plan yet.</div>
          </div>
        </article>
        <article class="operator-panel">
          <h3>Step Results</h3>
          <div id="step-list" class="operator-list">
            <div class="operator-copy">No steps executed yet.</div>
          </div>
        </article>
      </section>

      <section id="chat-shell" class="chat-shell">
        <section id="chat-empty" class="chat-empty">
          <div class="chat-empty-kicker">Guest mode ready</div>
          <h3>Hey, I'm FORGE - your AI agent.</h3>
          <p>
            I can chat, create files, inspect a workspace, and turn notes into verified artifacts. Try the local demo or ask: "Create notes.txt with content hello forge".
          </p>
        </section>

        <section id="demo-task" class="demo-task">
          <div class="demo-task-title">Try a local agent demo</div>
          <div class="demo-task-copy">FORGE will read demo_input.md, create action_items.md, and stream each execution step.</div>
          <div class="demo-task-actions">
            <button id="run-demo-task" type="button" class="button-ghost">Run Demo</button>
            <span id="demo-task-status" class="demo-task-status">No provider required.</span>
          </div>
        </section>

        <section id="chat" class="chat"></section>
        <div id="result-panel"></div>
        <button id="diagnostics-toggle" class="diagnostics-toggle hidden" type="button">Show technical details</button>
        <pre id="diagnostics-panel" class="diagnostics-panel hidden"></pre>

        <section class="composer">
          <div id="live-progress" class="live-progress hidden">
            <div id="live-status" class="live-status">Idle</div>
            <div id="live-steps" class="live-steps"></div>
          </div>
          <div class="composer-grid">
            <textarea id="prompt" placeholder="Message FORGE..."></textarea>
            <div class="actions">
              <button id="send" type="button">Send</button>
              <button id="clear" type="button">New Chat</button>
            </div>
          </div>
          <div class="footnote">
            Press Enter to send. Use Shift+Enter for a new line. Real file and shell actions stay confined to the selected workspace.
          </div>
        </section>
      </section>
    </main>
  </div>

  <script>
    const chat = document.getElementById("chat");
    const promptBox = document.getElementById("prompt");
    const sendButton = document.getElementById("send");
    const clearButton = document.getElementById("clear");
    const sidebarToggle = document.getElementById("sidebar-toggle");
    const sidebarScrim = document.getElementById("sidebar-scrim");
    const notes = document.getElementById("notes");
    const workerServices = document.getElementById("worker-services");
    const operatorMode = document.getElementById("operator-mode");
    const workspaceName = document.getElementById("workspace-name");
    const workspacePath = document.getElementById("workspace-path");
    const workspaceSummary = document.getElementById("workspace-summary");
    const applyWorkspaceButton = document.getElementById("apply-workspace");
    const browseWorkspaceButton = document.getElementById("browse-workspace");
    const confirmMode = document.getElementById("confirm-mode");
    const dryRunMode = document.getElementById("dry-run-mode");
    const objective = document.getElementById("objective");
    const objectiveNote = document.getElementById("objective-note");
    const validationState = document.getElementById("validation-state");
    const validationNote = document.getElementById("validation-note");
    const stepMetrics = document.getElementById("step-metrics");
    const stepNote = document.getElementById("step-note");
    const nextAction = document.getElementById("next-action");
    const nextNote = document.getElementById("next-note");
    const planList = document.getElementById("plan-list");
    const stepList = document.getElementById("step-list");
    const resultPanel = document.getElementById("result-panel");
    const diagnosticsToggle = document.getElementById("diagnostics-toggle");
    const diagnosticsPanel = document.getElementById("diagnostics-panel");
    const liveProgress = document.getElementById("live-progress");
    const liveStatus = document.getElementById("live-status");
    const liveSteps = document.getElementById("live-steps");
    const workspaceSubtitle = document.getElementById("workspace-subtitle");
    const authGate = document.getElementById("auth-gate");
    const authGateGoogleButton = document.getElementById("auth-gate-google");
    const authGateAccountButton = document.getElementById("auth-gate-account");
    const providerSetup = document.getElementById("provider-setup");
    const providerSetupGroq = document.getElementById("provider-setup-groq");
    const providerSetupOllama = document.getElementById("provider-setup-ollama");
    const providerSetupByok = document.getElementById("provider-setup-byok");
    const providerSetupStatus = document.getElementById("provider-setup-status");
    const chatShell = document.getElementById("chat-shell");
    const chatEmpty = document.getElementById("chat-empty");
    const demoTask = document.getElementById("demo-task");
    const runDemoTaskButton = document.getElementById("run-demo-task");
    const demoTaskStatus = document.getElementById("demo-task-status");
    const accountSummary = document.getElementById("account-summary");
    const authLoggedOut = document.getElementById("auth-logged-out");
    const authLoggedIn = document.getElementById("auth-logged-in");
    const authName = document.getElementById("auth-name");
    const authEmail = document.getElementById("auth-email");
    const authPassword = document.getElementById("auth-password");
    const loginButton = document.getElementById("login-button");
    const registerButton = document.getElementById("register-button");
    const googleLoginButton = document.getElementById("google-login-button");
    const browserSetupButton = document.getElementById("browser-setup-button");
    const logoutButton = document.getElementById("logout-button");
    const refreshAccountButton = document.getElementById("refresh-account-button");
    const sendVerificationButton = document.getElementById("send-verification-button");
    const requestResetButton = document.getElementById("request-reset-button");
    const verifyEmailButton = document.getElementById("verify-email-button");
    const applyResetButton = document.getElementById("apply-reset-button");
    const accountEmail = document.getElementById("account-email");
    const accountRole = document.getElementById("account-role");
    const accountManagerGate = document.getElementById("account-manager-gate");
    const accountEmailVerification = document.getElementById("account-email-verification");
    const authToken = document.getElementById("auth-token");
    const authNewPassword = document.getElementById("auth-new-password");
    const authStatus = document.getElementById("auth-status");
    const providerSelect = document.getElementById("provider-select");
    const providerApiKey = document.getElementById("provider-api-key");
    const providerAccountId = document.getElementById("provider-account-id");
    const providerOrganization = document.getElementById("provider-organization");
    const providerProject = document.getElementById("provider-project");
    const saveProviderKeyButton = document.getElementById("save-provider-key");
    const refreshProviderKeysButton = document.getElementById("refresh-provider-keys");
    const providerStatus = document.getElementById("provider-status");
    const providerSecretList = document.getElementById("provider-secret-list");
    const adminPanel = document.getElementById("admin-panel");
    const adminSummary = document.getElementById("admin-summary");
    const adminUsers = document.getElementById("admin-users");
    let currentUser = null;
    let pendingDeviceLogin = null;
    let pendingDevicePoll = null;
    let activeStream = null;
    let pendingUserDisplayText = "";
    let demoStreamActive = false;

    function openSidebar() {
      document.body.classList.add("sidebar-open");
    }

    function closeSidebar() {
      document.body.classList.remove("sidebar-open");
    }

    function toggleSidebar() {
      document.body.classList.toggle("sidebar-open");
    }

    function appendNote(line) {
      notes.textContent += "\\n" + line;
      notes.scrollTop = notes.scrollHeight;
    }

    function updateConversationLayout() {
      const hasMessages = chat.querySelector(".bubble") !== null;
      chatEmpty.classList.toggle("hidden", hasMessages);
      chatShell.classList.remove("guest-mode");
    }

    function clearNode(node) {
      while (node.firstChild) {
        node.removeChild(node.firstChild);
      }
    }

    function setAuthStatus(message) {
      authStatus.textContent = message || "Awaiting account action.";
    }

    function authMessage(prefix, payload) {
      const lines = [prefix];
      if (payload && payload.delivery_mode) lines.push("delivery=" + payload.delivery_mode);
      if (payload && payload.email) lines.push("email=" + payload.email);
      if (payload && payload.debug_token) lines.push("debug_token=" + payload.debug_token);
      return lines.join("\\n");
    }

    function stopDevicePolling() {
      if (pendingDevicePoll) {
        window.clearTimeout(pendingDevicePoll);
        pendingDevicePoll = null;
      }
      pendingDeviceLogin = null;
    }

    async function pollDeviceLogin() {
      if (!pendingDeviceLogin || !pendingDeviceLogin.device_code) {
        return;
      }
      try {
        const response = await fetch(`/api/auth/device/status?device_code=${encodeURIComponent(pendingDeviceLogin.device_code)}`);
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Desktop sign-in status failed.");
        }
        if (data.status === "approved" && data.authenticated && data.user) {
          stopDevicePolling();
          applyAuthState(data);
          setAuthStatus("Browser sign-in completed. Desktop session is active.");
          await Promise.all([loadProviderKeys(), loadAdmin(), loadBootStatus(), loadWorkspace(), loadWorkerTelemetry()]);
          addBubble("assistant", "Desktop onboarding completed. FORGE imported your authenticated account and is ready to run missions.");
          return;
        }
        if (data.status === "expired" || data.status === "rejected") {
          stopDevicePolling();
          setAuthStatus("Desktop sign-in expired. Start secure setup again.");
          return;
        }
        setAuthStatus("Waiting for browser sign-in to finish...");
        pendingDevicePoll = window.setTimeout(pollDeviceLogin, (pendingDeviceLogin.interval_seconds || 2) * 1000);
      } catch (error) {
        stopDevicePolling();
        setAuthStatus(error.message || "Desktop sign-in failed.");
      }
    }

    async function startDeviceLogin(mode) {
      stopDevicePolling();
      const setupWindow = window.open("about:blank", "_blank");
      try {
        const response = await fetch("/api/auth/device/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: authName.value.trim(), mode })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Desktop sign-in could not start.");
        }
        pendingDeviceLogin = data;
        if (setupWindow) {
          setupWindow.location.replace(data.verification_url);
        } else {
          window.open(data.verification_url, "_blank", "noopener");
        }
        setAuthStatus("Secure browser setup opened. Finish sign-in there and FORGE Desktop will unlock automatically.");
        pendingDevicePoll = window.setTimeout(pollDeviceLogin, (data.interval_seconds || 2) * 1000);
      } catch (error) {
        if (setupWindow) {
          setupWindow.close();
        }
        setAuthStatus(error.message || "Desktop sign-in could not start.");
      }
    }

    function requireAuthenticated() {
      if (currentUser) return true;
      addBubble("error", "Sign in is only needed for sync, saved provider keys, admin, and private portal features.");
      setAuthStatus("Guest mode is active. Sign in only for private features.");
      return false;
    }

    function renderProviderSetup(setup) {
      const needsSetup = !!(currentUser && setup && setup.needs_provider_setup);
      providerSetup.classList.toggle("hidden", !needsSetup);
      if (!currentUser) {
        providerSetup.classList.add("hidden");
        providerSetupStatus.textContent = "Guest mode is ready. Provider setup is optional.";
        return;
      }
      if (!setup) {
        providerSetupStatus.textContent = "Checking provider readiness...";
        return;
      }

      const ollama = setup.ollama || {};
      if (needsSetup) {
        const ollamaLine = ollama.running
          ? "Ollama is running with " + String(ollama.model_count || 0) + " local model(s)."
          : "Ollama is not running at " + (ollama.url || "http://localhost:11434/api/tags") + ".";
        providerSetupStatus.textContent =
          ollamaLine +
          "\\nSaved cloud provider keys: " + String(setup.saved_provider_count || 0) +
          "\\nRecommended: Groq for fastest first success, or Ollama if you want local-only.";
        workspaceSubtitle.textContent = "First run needs one model path. Choose Groq, start Ollama, or bring your own key.";
        resultPanel.textContent = "Provider setup required before general model reasoning. Local file/workspace skills remain protected by workspace rules.";
      } else {
        providerSetupStatus.textContent = setup.cloud_provider_ready
          ? "Cloud provider key is saved."
          : "Ollama is running locally.";
      }
    }

    function chooseProviderSetup(kind) {
      if (kind === "groq") {
        providerSelect.value = "groq";
        providerStatus.textContent = "Groq selected. Paste a Groq API key, then Save Key.";
        providerApiKey.placeholder = "Groq API key, for example gsk_...";
        openSidebar();
        providerApiKey.focus();
        return;
      }
      if (kind === "ollama") {
        providerStatus.textContent = "Ollama local path selected. Start Ollama, run `ollama pull llama3.3`, then press Reload.";
        appendNote("Ollama check failed at http://localhost:11434/api/tags. Start Ollama locally and reload provider status.");
        openSidebar();
        return;
      }
      providerStatus.textContent = "Bring your own key: choose a provider, paste the key, then Save Key.";
      openSidebar();
      providerSelect.focus();
    }

    function renderProviderSecrets(items) {
      if (!items || !items.length) {
        setListPlaceholder(providerSecretList, currentUser ? "No provider keys saved yet." : "Login to manage your provider keys.");
        return;
      }
      clearNode(providerSecretList);
      items.forEach((item) => {
        const article = document.createElement("article");
        article.className = "mini-item";
        const title = document.createElement("div");
        title.className = "mini-title";
        title.textContent = item.provider;
        const body = document.createElement("div");
        body.className = "mini-copy";
        const previews = Object.entries(item.preview || {}).map(([key, value]) => key + ": " + value).join(" | ");
        body.textContent = (item.fields || []).join(", ") + (previews ? "\\n" + previews : "");
        article.appendChild(title);
        article.appendChild(body);
        providerSecretList.appendChild(article);
      });
    }

    function renderAdminUsers(users) {
      if (!users || !users.length) {
        setListPlaceholder(adminUsers, "No users yet.");
        return;
      }
      clearNode(adminUsers);
      users.forEach((user) => {
        const article = document.createElement("article");
        article.className = "mini-item";
        const title = document.createElement("div");
        title.className = "mini-title";
        title.textContent = user.email + (user.is_admin ? " | manager" : "");
        const body = document.createElement("div");
        body.className = "mini-copy";
        body.textContent =
          "display_name=" + (user.display_name || "-") +
          " | secret_sets=" + String(user.secret_count || 0) +
          " | created_at=" + (user.created_at || "-") +
          "\\nlast_login_at=" + (user.last_login_at || "never");
        article.appendChild(title);
        article.appendChild(body);
        adminUsers.appendChild(article);
      });
    }

    function applyAuthState(data) {
      const authenticated = !!(data && data.authenticated && data.user);
      currentUser = authenticated ? data.user : null;
      authLoggedOut.classList.toggle("hidden", authenticated);
      authLoggedIn.classList.toggle("hidden", !authenticated);
      authGate.classList.add("hidden");
      providerSetup.classList.add("hidden");
      demoTask.classList.remove("hidden");
      adminPanel.classList.toggle("hidden", !(authenticated && data.user.is_admin));
      sendButton.disabled = false;
      promptBox.disabled = false;

      if (authenticated) {
        stopDevicePolling();
        sidebarToggle.textContent = "Settings";
        closeSidebar();
        accountEmail.textContent = data.user.email;
        accountRole.textContent = data.user.is_admin ? "Manager" : "User";
        accountManagerGate.textContent = data.user.is_admin ? "Open" : "Closed";
        accountEmailVerification.textContent = data.user.email_verified ? "Verified" : "Pending";
        accountSummary.textContent = "Each account keeps its own encrypted provider secrets. FORGE must use the signed-in user's keys.";
        setAuthStatus("Signed in as " + data.user.email);
        workspaceSubtitle.textContent = "Ask naturally. FORGE will answer like a real assistant, and execute only when your request requires tools.";
        promptBox.placeholder = "Message FORGE...";
        promptBox.focus();
      } else {
        sidebarToggle.textContent = "Settings";
        closeSidebar();
        accountSummary.textContent = "Guest mode is ready. Sign in only for sync, saved cloud keys, admin, or private portal features.";
        accountEmail.textContent = "-";
        accountRole.textContent = "User";
        accountManagerGate.textContent = "Closed";
        accountEmailVerification.textContent = "Pending";
        providerStatus.textContent = "Optional: sign in to save encrypted cloud provider keys. The local demo does not need a key.";
        renderProviderSecrets([]);
        setListPlaceholder(workerServices, "Local worker telemetry is available in guest mode.");
        workspaceName.textContent = "Guest workspace";
        workspaceSummary.textContent = "Choose a local workspace or run the demo immediately. Account sync is optional.";
        workspaceSubtitle.textContent = "Guest mode is ready. Try the demo or ask FORGE to create a file in your workspace.";
        promptBox.placeholder = "Message FORGE...";
      }
      updateConversationLayout();
    }

    async function loadAuth() {
      const response = await fetch("/api/auth/me");
      const data = await response.json();
      applyAuthState(data);
      return data;
    }

    async function authRequest(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {})
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Authentication request failed.");
      }
      applyAuthState(data);
      if (data.verification) {
        setAuthStatus(authMessage("Verification queued.", data.verification));
      }
      return data;
    }

    async function loadProviderKeys() {
      if (!currentUser) {
        renderProviderSecrets([]);
        return;
      }
      const response = await fetch("/api/user/keys");
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Failed to load provider keys.");
      }
      renderProviderSecrets(data.saved || []);
      providerStatus.textContent = "Saved encrypted provider key sets: " + String((data.saved || []).length);
    }

    async function saveProviderKey() {
      if (!requireAuthenticated()) return;
      const provider = providerSelect.value;
      const payload = { provider };
      if (providerApiKey.value.trim()) payload.api_key = providerApiKey.value.trim();
      if (providerAccountId.value.trim()) payload.account_id = providerAccountId.value.trim();
      if (providerOrganization.value.trim()) {
        if (provider === "cloudflare") payload.email = providerOrganization.value.trim();
        else payload.organization = providerOrganization.value.trim();
      }
      if (providerProject.value.trim()) {
        if (provider === "cloudflare") payload.global_key = providerProject.value.trim();
        else payload.project = providerProject.value.trim();
      }
      const response = await fetch("/api/user/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Saving provider key failed.");
      }
      renderProviderSecrets(data.saved || []);
      providerStatus.textContent = "Saved key set for " + provider + ".";
      providerApiKey.value = "";
      providerAccountId.value = "";
      providerOrganization.value = "";
      providerProject.value = "";
      closeSidebar();
      await loadBootStatus();
    }

    async function loadAdmin() {
      if (!(currentUser && currentUser.is_admin)) {
        return;
      }
      const [overviewResponse, usersResponse] = await Promise.all([
        fetch("/api/admin/overview"),
        fetch("/api/admin/users")
      ]);
      const overviewData = await overviewResponse.json();
      const usersData = await usersResponse.json();
      if (!overviewResponse.ok) {
        throw new Error(overviewData.error || "Failed to load admin overview.");
      }
      if (!usersResponse.ok) {
        throw new Error(usersData.error || "Failed to load admin users.");
      }
      const overview = overviewData.overview || {};
      adminSummary.textContent =
        "users=" + String(overview.users || 0) +
        " | active_sessions=" + String(overview.active_sessions || 0) +
        " | stored_provider_sets=" + String(overview.stored_provider_sets || 0) +
        " | workers=" + String(overview.workers || 0) +
        " | pending_approvals=" + String(overview.pending_approvals || 0);
      renderAdminUsers(usersData.users || []);
    }

    function createBubble(role, text = "") {
      const article = document.createElement("article");
      article.className = "bubble " + role;

      const header = document.createElement("header");
      header.textContent = role === "user" ? "You" : role === "error" ? "FORGE Error" : "FORGE";

      const body = document.createElement("div");
      body.className = "body";
      const footer = document.createElement("div");
      footer.className = "bubble-footer hidden";

      article.appendChild(header);
      article.appendChild(body);
      article.appendChild(footer);
      article._body = body;
      article._footer = footer;
      setBubbleText(article, text, false);
      chat.appendChild(article);
      chat.scrollTop = chat.scrollHeight;
      updateConversationLayout();
      return article;
    }

    function setBubbleText(article, text, streaming) {
      const body = article._body;
      clearNode(body);
      body.appendChild(document.createTextNode(text || ""));
      article.classList.toggle("streaming", !!streaming);
      if (streaming) {
        const cursor = document.createElement("span");
        cursor.className = "cursor";
        cursor.textContent = "|";
        body.appendChild(cursor);
      }
    }

    function setBubbleFooter(article, text) {
      if (!article || !article._footer) return;
      article._footer.textContent = text || "";
      article._footer.classList.toggle("hidden", !text);
    }

    function resetLiveProgress() {
      liveProgress.classList.add("hidden");
      liveProgress.classList.remove("active");
      liveStatus.textContent = "Idle";
      clearNode(liveSteps);
    }

    function setLiveStatus(text) {
      const clean = String(text || "").trim();
      if (!clean) return;
      liveProgress.classList.remove("hidden");
      liveProgress.classList.add("active");
      liveStatus.textContent = clean;
    }

    function renderLivePlan(steps) {
      clearNode(liveSteps);
      const list = Array.isArray(steps) ? steps : [];
      list.forEach((step, index) => {
        const item = document.createElement("div");
        item.className = "live-step pending";
        item.dataset.step = String(index + 1);

        const label = document.createElement("span");
        label.className = "live-step-label";
        label.textContent = typeof step === "string" ? step : (step.label || step.action || "Step " + String(index + 1));

        const state = document.createElement("span");
        state.className = "live-step-state";
        state.textContent = "pending";

        item.appendChild(label);
        item.appendChild(state);
        liveSteps.appendChild(item);
      });
    }

    function updateLiveStep(step, state, label, ms) {
      const index = Number(step);
      let item = Number.isFinite(index) && index > 0 ? liveSteps.querySelector('[data-step="' + String(index) + '"]') : null;
      if (!item && liveSteps.children.length === 1) item = liveSteps.children[0];
      if (!item) return;
      item.className = "live-step " + state;
      const labelNode = item.querySelector(".live-step-label");
      const stateNode = item.querySelector(".live-step-state");
      if (labelNode && label) labelNode.textContent = label;
      if (stateNode) {
        if (state === "done") stateNode.textContent = ms ? "done " + String(Math.round(ms)) + "ms" : "done";
        else if (state === "failed") stateNode.textContent = "needs recovery";
        else stateNode.textContent = "running";
      }
    }

    function addBubble(role, text) {
      return createBubble(role, text);
    }

    function closeActiveStream() {
      if (activeStream) {
        activeStream.close();
        activeStream = null;
      }
    }

    function setMissionStatus(value) {
      validationState.textContent = value || "Idle";
      validationState.className = "operator-value";
      if (value) {
        validationState.classList.add("status-pill", value);
      }
    }

    function setListPlaceholder(node, message) {
      clearNode(node);
      const item = document.createElement("div");
      item.className = "operator-copy";
      item.textContent = message;
      node.appendChild(item);
    }

    function renderWorkers(payload) {
      const services = payload && payload.workers && payload.workers.services ? payload.workers.services : [];
      if (!services.length) {
        setListPlaceholder(workerServices, "No worker telemetry yet.");
        return;
      }

      clearNode(workerServices);
      services.forEach((service) => {
        const row = document.createElement("article");
        row.className = "worker-row";

        const header = document.createElement("div");
        header.className = "worker-header";

        const name = document.createElement("div");
        name.className = "worker-name";
        name.textContent = service.service || "unknown-service";

        const state = document.createElement("div");
        const status = service.status || "idle";
        state.className = "worker-state " + status;
        state.textContent = status;

        const meta = document.createElement("div");
        meta.className = "worker-meta";
        meta.textContent =
          "active_jobs=" + String(service.active_jobs || 0) +
          " | queued_jobs=" + String(service.queued_jobs || 0) +
          " | queue_length=" + String(service.queue_length || 0) +
          " | avg_processing_ms=" + String(service.avg_processing_ms || 0) +
          "\\n" +
          (service.workers || []).map((worker) =>
            worker.lane_id + " [" + String(worker.process_mode || worker.location || "worker") + "]" +
            ": active=" + String(worker.active_jobs || 0) +
            ", queued=" + String(worker.queued_jobs || 0) +
            ", queue=" + String(worker.queue_length || 0) +
            ", processed=" + String(worker.processed_jobs || 0) +
            ", avg_ms=" + String(worker.avg_processing_ms || 0) +
            ", mem_mb=" + String(worker.mem_usage_mb || 0)
          ).join("\\n");

        header.appendChild(name);
        header.appendChild(state);
        row.appendChild(header);
        row.appendChild(meta);
        workerServices.appendChild(row);
      });
    }

    function renderWorkspace(data) {
      const root = data && data.workspace_root ? data.workspace_root : "";
      const artifactRoot = data && data.artifact_root ? data.artifact_root : "";
      const keyFiles = data && data.key_files ? data.key_files.slice(0, 6) : [];
      workspaceName.textContent = data && data.workspace_name ? data.workspace_name : (root || "Not set");
      workspacePath.value = root;
      workspaceSummary.textContent =
        "Artifacts: " + (artifactRoot || "-") +
        " | Files: " + String(data && data.file_count ? data.file_count : 0) +
        (keyFiles.length ? "\\nKey files: " + keyFiles.join(" | ") : "\\nNo key files indexed yet.");
    }

    function clip(value, limit = 700) {
      if (value === null || value === undefined) return "No output.";
      const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      return text.length > limit ? text.slice(0, limit) + "..." : text;
    }

    function renderPlan(plan) {
      if (!plan || !plan.steps || !plan.steps.length) {
        setListPlaceholder(planList, "No execution plan yet.");
        return;
      }

      clearNode(planList);
      plan.steps.forEach((step) => {
        const item = document.createElement("article");
        item.className = "operator-item";

        const title = document.createElement("div");
        title.className = "operator-item-title";
        title.textContent = step.id + " | " + step.action;

        const meta = document.createElement("div");
        meta.className = "operator-item-meta";
        meta.textContent = "Skill: " + (step.skill || "reasoning") + " | Expected: " + step.expected_output;

        const body = document.createElement("div");
        body.className = "operator-item-body";
        body.textContent = "Validation: " + step.validation + (step.fallback_skill ? " | Fallback: " + step.fallback_skill : "");

        item.appendChild(title);
        item.appendChild(meta);
        item.appendChild(body);
        planList.appendChild(item);
      });
    }

    function renderSteps(stepResults) {
      if (!stepResults || !stepResults.length) {
        setListPlaceholder(stepList, "No steps executed yet.");
        return;
      }

      clearNode(stepList);
      stepResults.forEach((step) => {
        const item = document.createElement("article");
        item.className = "operator-item";

        const title = document.createElement("div");
        title.className = "operator-item-title";
        title.textContent = step.step_id + " | " + (step.skill || "reasoning");

        const meta = document.createElement("div");
        meta.className = "operator-item-meta";
        meta.textContent = "Status: " + step.status + " | Attempts: " + step.attempts + " | Validation: " + step.validation_status;

        const body = document.createElement("div");
        body.className = "operator-item-body";
        const notesText = step.validation_notes && step.validation_notes.length ? "\\nNotes: " + step.validation_notes.join(" | ") : "";
        const evidenceText = step.evidence && step.evidence.length ? "\\nEvidence: " + step.evidence.slice(0, 6).join(" | ") : "";
        body.textContent = clip(step.output, 600) + notesText + evidenceText;

        item.appendChild(title);
        item.appendChild(meta);
        item.appendChild(body);
        stepList.appendChild(item);
      });
    }

    function compactDiagnostics(data) {
      if (data.technical_details) {
        return data.technical_details;
      }
      if (data.diagnostics) {
        return data.diagnostics;
      }
      const diagnostics = {};
      ["mission_id", "audit_log_path", "intent", "plan", "step_results", "mission_trace", "agent_reviews", "provider_telemetry"].forEach((key) => {
        if (data[key]) diagnostics[key] = data[key];
      });
      if (data.artifacts && typeof data.artifacts === "object") {
        diagnostics.artifact_keys = Object.keys(data.artifacts);
      }
      return Object.keys(diagnostics).length ? diagnostics : null;
    }

    function renderDiagnostics(data) {
      const diagnostics = compactDiagnostics(data || {});
      if (!diagnostics) {
        diagnosticsToggle.classList.add("hidden");
        diagnosticsPanel.classList.add("hidden");
        diagnosticsPanel.textContent = "";
        diagnosticsToggle.textContent = "Show technical details";
        return;
      }
      diagnosticsPanel.textContent = JSON.stringify(diagnostics, null, 2);
      diagnosticsPanel.classList.add("hidden");
      diagnosticsToggle.classList.remove("hidden");
      diagnosticsToggle.textContent = "Show technical details";
    }

    function renderOperatorResult(data) {
      objective.textContent = data.objective || "No active objective.";
      objectiveNote.textContent = data.intent && data.intent.hidden_intent
        ? data.intent.hidden_intent
        : "No hidden intent detected.";
      setMissionStatus(data.validation_status || "idle");
      validationNote.textContent = data.risks_or_limitations && data.risks_or_limitations.length
        ? data.risks_or_limitations.slice(0, 2).join(" | ")
        : "No major limitations reported.";
      stepMetrics.textContent = String(data.completed_steps || 0) + " / " + String(data.total_steps || 0);
      stepNote.textContent = "Evidence: " + String(data.evidence_count || 0) + " | Artifacts: " + String(data.artifacts_count || 0);
      nextAction.textContent = data.best_next_action || "No next action.";
      nextNote.textContent = data.approach_taken && data.approach_taken.length
        ? data.approach_taken.join(" | ")
        : "No approach notes.";
      resultPanel.textContent = data.user_response || data.answer || data.result || "No result produced.";
      workspaceSubtitle.textContent = data.best_next_action || "FORGE responded successfully.";
      renderDiagnostics(data);
      renderPlan(data.plan);
      renderSteps(data.step_results);
    }

    async function loadBootStatus() {
      try {
        const response = await fetch("/api/boot");
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Boot failed.");
        }
        document.getElementById("runtime-state").textContent = data.provider_setup && data.provider_setup.needs_provider_setup ? "Setup" : (data.models_online > 0 ? "Ready" : "Limited");
        document.getElementById("providers").textContent = String(data.providers);
        document.getElementById("models").textContent = String(data.models_online);
        document.getElementById("version").textContent = data.version;
        appendNote(data.summary);
        renderProviderSetup(data.provider_setup);
        if (data.workspace_root) {
          workspacePath.value = data.workspace_root;
        }
      } catch (error) {
        document.getElementById("runtime-state").textContent = "Failed";
        appendNote("Boot request failed: " + error.message);
      }
    }

    async function loadWorkspace() {
      try {
        const response = await fetch("/api/workspace");
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Workspace load failed.");
        }
        renderWorkspace(data);
      } catch (error) {
        workspaceName.textContent = "Unavailable";
        workspaceSummary.textContent = "Workspace load failed: " + error.message;
      }
    }

    async function applyWorkspace(path) {
      try {
        const response = await fetch("/api/workspace", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace_root: path })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Workspace update failed.");
        }
        renderWorkspace(data);
        appendNote("Workspace set to " + data.workspace_root);
        closeSidebar();
      } catch (error) {
        appendNote("Workspace update failed: " + error.message);
        addBubble("error", "Workspace update failed: " + error.message);
      }
    }

    async function browseWorkspace() {
      browseWorkspaceButton.disabled = true;
      browseWorkspaceButton.textContent = "Opening...";
      try {
        const response = await fetch("/api/workspace/dialog", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Workspace picker failed.");
        }
        renderWorkspace(data);
        if (!data.cancelled) {
          appendNote("Workspace selected via native picker: " + data.workspace_root);
          closeSidebar();
        }
      } catch (error) {
        appendNote("Workspace picker failed: " + error.message);
        addBubble("error", "Workspace picker failed: " + error.message);
      } finally {
        browseWorkspaceButton.disabled = false;
        browseWorkspaceButton.textContent = "Browse...";
      }
    }

    async function runDemoTask() {
      runDemoTaskButton.disabled = true;
      runDemoTaskButton.textContent = "Preparing...";
      demoTaskStatus.textContent = "Creating safe demo workspace...";
      try {
        const response = await fetch("/api/demo/prepare", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Demo setup failed.");
        }
        renderWorkspace(data);
        workspacePath.value = data.workspace_root || workspacePath.value;
        confirmMode.checked = true;
        dryRunMode.checked = false;
        pendingUserDisplayText = "Run local demo: demo_input.md -> action_items.md";
        demoStreamActive = true;
        promptBox.value = data.demo && data.demo.prompt ? data.demo.prompt : "";
        demoTaskStatus.textContent = "Streaming demo execution...";
        appendNote("Demo workspace prepared: " + (data.workspace_root || ""));
        await sendPrompt();
      } catch (error) {
        demoTaskStatus.textContent = error.message || "Demo setup failed.";
        addBubble("error", "Demo setup failed: " + (error.message || "Unknown error"));
        runDemoTaskButton.disabled = false;
        runDemoTaskButton.textContent = "Run Demo";
        demoStreamActive = false;
      }
    }

    async function loadWorkerTelemetry() {
      if (!currentUser) {
        try {
          const response = await fetch("/api/workers");
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || "Worker telemetry load failed.");
          }
          renderWorkers(data);
        } catch (error) {
          setListPlaceholder(workerServices, "Local worker telemetry unavailable: " + error.message);
        }
        return;
      }
      try {
        const response = await fetch("/api/workers");
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Worker telemetry load failed.");
        }
        renderWorkers(data);
      } catch (error) {
        setListPlaceholder(workerServices, "Worker telemetry unavailable: " + error.message);
      }
    }

    async function sendPrompt() {
      const prompt = promptBox.value.trim();
      if (!prompt || sendButton.disabled) return;

      closeActiveStream();
      const displayPrompt = pendingUserDisplayText || prompt;
      pendingUserDisplayText = "";
      addBubble("user", displayPrompt);
      appendNote("Routing request inside workspace: " + (workspacePath.value.trim() || "<unset>"));
      promptBox.value = "";
      sendButton.disabled = true;
      sendButton.textContent = "Sending...";
      setMissionStatus("running");
      closeSidebar();
      resultPanel.textContent = "FORGE is preparing the response...";
      renderDiagnostics({});
      resetLiveProgress();
      setLiveStatus("Analyzing your request...");

      const assistantBubble = createBubble("assistant", "");
      let streamedText = "";
      let streamFinished = false;
      const liveEvents = [];

      const params = new URLSearchParams({
        prompt,
        confirmed: confirmMode.checked ? "true" : "false",
        dry_run: dryRunMode.checked ? "true" : "false",
        workspace_root: workspacePath.value.trim(),
      });

      const stream = new EventSource("/api/stream?" + params.toString());
      activeStream = stream;

      function finishStream() {
        streamFinished = true;
        closeActiveStream();
        sendButton.disabled = false;
        sendButton.textContent = "Send";
        runDemoTaskButton.disabled = false;
        runDemoTaskButton.textContent = "Run Demo";
        if (demoStreamActive) {
          demoTaskStatus.textContent = "Demo complete. Open action_items.md in the selected workspace.";
          demoStreamActive = false;
        }
        promptBox.focus();
      }

      function pushLiveEvent(message) {
        const clean = String(message || "").trim();
        if (!clean) return;
        liveEvents.push(clean);
        if (!streamedText) {
          setBubbleText(assistantBubble, liveEvents.slice(-8).join("\\n"), true);
        }
      }

      function stepLabel(data) {
        const prefix = data.index && data.total ? "Step " + data.index + "/" + data.total : (data.step_id || "Step");
        const tool = data.skill || data.tool || "reasoning";
        return prefix + " - " + tool;
      }

      function compactArtifacts(paths) {
        if (!paths || !paths.length) return "";
        return " | Artifacts: " + paths.slice(0, 3).join(", ");
      }

      stream.onmessage = (event) => {
        const data = JSON.parse(event.data || "{}");

        if (data.type === "start") {
          workspaceSubtitle.textContent = "Response path ready.";
          if (!streamedText) {
            setBubbleText(assistantBubble, "Preparing the response...", true);
          }
          return;
        }

        if (data.type === "intent_analyzing") {
          setMissionStatus("running");
          const text = data.text || data.message || "Analyzing your request...";
          workspaceSubtitle.textContent = text;
          setLiveStatus(text);
          pushLiveEvent(text);
          return;
        }

        if (data.type === "plan") {
          setMissionStatus("running");
          const text = data.message || "Plan ready.";
          workspaceSubtitle.textContent = text;
          resultPanel.textContent = text;
          setLiveStatus(text);
          renderLivePlan(data.structured_steps || data.steps || []);
          pushLiveEvent(text);
          return;
        }

        if (data.type === "plan_ready") {
          setMissionStatus("running");
          workspaceSubtitle.textContent = data.message || "Plan ready.";
          resultPanel.textContent = data.message || "Plan ready.";
          renderPlan(data.plan || { steps: data.steps || [] });
          renderLivePlan(data.steps || []);
          if (data.visible !== false) {
            pushLiveEvent(data.message || "Plan ready.");
          }
          return;
        }

        if (data.type === "step_start") {
          setMissionStatus("running");
          const text = data.text || data.message || "Starting step...";
          setLiveStatus(text);
          updateLiveStep(data.step, "running", data.label || text, 0);
          workspaceSubtitle.textContent = text;
          pushLiveEvent(text);
          return;
        }

        if (data.type === "step_done") {
          const text = data.text || data.message || "Step complete.";
          setLiveStatus(text);
          updateLiveStep(data.step, "done", data.label || text, data.ms || 0);
          workspaceSubtitle.textContent = text;
          pushLiveEvent(text + (data.ms ? " (" + String(Math.round(data.ms)) + "ms)" : ""));
          return;
        }

        if (data.type === "step_started") {
          setMissionStatus("running");
          const line = "Starting " + stepLabel(data) + (data.action ? ": " + data.action : "");
          workspaceSubtitle.textContent = line;
          setLiveStatus(line);
          updateLiveStep(data.index || data.step, "running", data.action || data.label || line, 0);
          pushLiveEvent(line);
          return;
        }

        if (data.type === "step_completed") {
          const line = "Completed " + stepLabel(data) + " | evidence=" + String(data.evidence_count || 0);
          workspaceSubtitle.textContent = line;
          setLiveStatus(line);
          updateLiveStep(data.index || data.step, "done", data.label || line, data.ms || 0);
          pushLiveEvent(line);
          return;
        }

        if (data.type === "step_failed") {
          const line = data.text || "Failed " + stepLabel(data) + ": " + (data.error || data.status || "unknown failure");
          workspaceSubtitle.textContent = line;
          setLiveStatus(line);
          updateLiveStep(data.index || data.step, "failed", data.label || line, data.ms || 0);
          pushLiveEvent(line);
          setMissionStatus("failed");
          return;
        }

        if (data.type === "provider_selected") {
          workspaceSubtitle.textContent = "Execution path ready.";
          pushLiveEvent("Execution path ready.");
          return;
        }

        if (data.type === "provider_timeout") {
          pushLiveEvent("A response path was slow. Trying another route...");
          appendNote("A response path was slow. FORGE is trying another route.");
          return;
        }

        if (data.type === "provider_fallback") {
          pushLiveEvent("Retrying with another route...");
          appendNote("FORGE is retrying with another route.");
          return;
        }

        if (data.type === "mission_completed") {
          setMissionStatus(data.success ? "finished" : "failed");
          const latency = data.total_latency_ms ? (Number(data.total_latency_ms) / 1000).toFixed(1) + "s" : "unknown latency";
          const footer = "FORGE | " + latency;
          workspaceSubtitle.textContent = data.message || "Mission completed.";
          resultPanel.textContent = (data.message || "Mission completed.") + compactArtifacts(data.artifact_paths || []);
          setLiveStatus(data.success ? "Mission complete." : "Mission ended with a recoverable issue.");
          setBubbleFooter(assistantBubble, footer);
          pushLiveEvent((data.message || "Mission completed.") + compactArtifacts(data.artifact_paths || []));
          return;
        }

        if (data.type === "status") {
          setMissionStatus("running");
          const text = data.text || data.message || "FORGE is working...";
          workspaceSubtitle.textContent = text;
          setLiveStatus(text);
          if (!streamedText) {
            pushLiveEvent(text);
          }
          return;
        }

        if (data.type === "delta") {
          streamedText += data.delta || "";
          setBubbleText(assistantBubble, streamedText, true);
          return;
        }

        if (data.type === "result" || data.type === "user_response") {
          streamedText = data.content || "";
          setBubbleText(assistantBubble, streamedText, false);
          setLiveStatus("Response ready.");
          resultPanel.textContent = streamedText || "Response ready.";
          if (data.has_details) {
            diagnosticsToggle.classList.remove("hidden");
            diagnosticsToggle.textContent = "Show technical details";
          }
          return;
        }

        if (data.type === "technical_details") {
          renderDiagnostics({ technical_details: data.content || {} });
          return;
        }

        if (data.type === "done") {
          const payload = data.payload || {};
          if (payload.workspace_root) {
            renderWorkspace(payload);
            renderOperatorResult(payload);
          }
          const finalText = streamedText || data.user_response || payload.user_response || payload.answer || payload.result || "No result produced.";
          setBubbleText(assistantBubble, finalText, false);
          setBubbleFooter(assistantBubble, data.footer || payload.stream_footer || "");
          setLiveStatus("Done.");
          appendNote("Response completed successfully.");
          finishStream();
          return;
        }

        if (data.type === "error") {
          assistantBubble.className = "bubble error";
          setBubbleText(assistantBubble, data.error || "Execution failed.", false);
          setMissionStatus("failed");
          appendNote("Execution failed: " + (data.error || "Unknown error"));
          resultPanel.textContent = "Execution failed. Recovery path: check your provider keys, workspace path, and mission wording, then retry.";
          finishStream();
        }
      };

      stream.onerror = () => {
        if (streamFinished) {
          return;
        }
        assistantBubble.className = "bubble error";
        setBubbleText(assistantBubble, "Streaming connection failed before FORGE could finish the response.", false);
        setMissionStatus("failed");
        appendNote("Execution failed: streaming connection lost.");
        resultPanel.textContent = "Streaming failed. Retry the mission, and if it repeats inspect provider keys and workspace configuration.";
        finishStream();
      };
    }

    sendButton.addEventListener("click", sendPrompt);
    providerSetupGroq.addEventListener("click", () => chooseProviderSetup("groq"));
    providerSetupOllama.addEventListener("click", () => chooseProviderSetup("ollama"));
    providerSetupByok.addEventListener("click", () => chooseProviderSetup("byok"));
    runDemoTaskButton.addEventListener("click", runDemoTask);
    authGateGoogleButton.addEventListener("click", async () => {
      await startDeviceLogin("google");
    });
    authGateAccountButton.addEventListener("click", () => {
      openSidebar();
      authEmail.focus();
    });
    sidebarToggle.addEventListener("click", toggleSidebar);
    sidebarScrim.addEventListener("click", closeSidebar);
    applyWorkspaceButton.addEventListener("click", () => applyWorkspace(workspacePath.value.trim()));
    browseWorkspaceButton.addEventListener("click", browseWorkspace);
    loginButton.addEventListener("click", async () => {
      try {
        await authRequest("/api/auth/login", {
          email: authEmail.value.trim(),
          password: authPassword.value
        });
        await Promise.all([loadBootStatus(), loadWorkspace(), loadProviderKeys(), loadWorkerTelemetry(), loadAdmin()]);
      } catch (error) {
        setAuthStatus(error.message);
        addBubble("error", error.message);
      }
    });
    registerButton.addEventListener("click", async () => {
      try {
        await authRequest("/api/auth/register", {
          display_name: authName.value.trim(),
          email: authEmail.value.trim(),
          password: authPassword.value
        });
        await Promise.all([loadBootStatus(), loadWorkspace(), loadProviderKeys(), loadWorkerTelemetry(), loadAdmin()]);
      } catch (error) {
        setAuthStatus(error.message);
        addBubble("error", error.message);
      }
    });
    googleLoginButton.addEventListener("click", async () => {
      await startDeviceLogin("google");
    });
    browserSetupButton.addEventListener("click", async () => {
      await startDeviceLogin("browser");
    });
    logoutButton.addEventListener("click", async () => {
      closeActiveStream();
      await fetch("/api/auth/logout", { method: "POST" });
      applyAuthState({ authenticated: false });
      setAuthStatus("Logged out.");
    });
    refreshAccountButton.addEventListener("click", async () => {
      try {
        await loadAuth();
        await Promise.all([loadBootStatus(), loadWorkspace(), loadProviderKeys(), loadWorkerTelemetry(), loadAdmin()]);
      } catch (error) {
        setAuthStatus(error.message);
      }
    });
    sendVerificationButton.addEventListener("click", async () => {
      try {
        const response = await fetch("/api/auth/request-verification", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Verification request failed.");
        setAuthStatus(authMessage("Verification queued.", data.verification || data));
      } catch (error) {
        setAuthStatus(error.message);
      }
    });
    requestResetButton.addEventListener("click", async () => {
      try {
        const response = await fetch("/api/auth/request-password-reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: authEmail.value.trim() || (currentUser ? currentUser.email : "") })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Reset request failed.");
        setAuthStatus(authMessage("Password reset requested.", data.reset || data));
      } catch (error) {
        setAuthStatus(error.message);
      }
    });
    verifyEmailButton.addEventListener("click", async () => {
      try {
        const response = await fetch("/api/auth/verify-email", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: authToken.value.trim() })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Email verification failed.");
        if (data.user) applyAuthState({ authenticated: true, user: data.user });
        setAuthStatus(data.message || "Email verified successfully.");
      } catch (error) {
        setAuthStatus(error.message);
      }
    });
    applyResetButton.addEventListener("click", async () => {
      try {
        const response = await fetch("/api/auth/reset-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: authToken.value.trim(), password: authNewPassword.value })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Password reset failed.");
        applyAuthState(data);
        setAuthStatus(data.message || "Password reset complete.");
        authToken.value = "";
        authNewPassword.value = "";
      } catch (error) {
        setAuthStatus(error.message);
      }
    });
    saveProviderKeyButton.addEventListener("click", async () => {
      try {
        await saveProviderKey();
      } catch (error) {
        providerStatus.textContent = error.message;
        addBubble("error", error.message);
      }
    });
    refreshProviderKeysButton.addEventListener("click", async () => {
      try {
        await loadProviderKeys();
        await loadBootStatus();
      } catch (error) {
        providerStatus.textContent = error.message;
      }
    });
    clearButton.addEventListener("click", () => {
      closeActiveStream();
      chat.innerHTML = "";
      updateConversationLayout();
      objective.textContent = "No active objective.";
      objectiveNote.textContent = "Submit a serious task to generate a real operator mission.";
      setMissionStatus("idle");
      validationNote.textContent = "Validation status appears here after execution.";
      stepMetrics.textContent = "0 / 0";
      stepNote.textContent = "Completed steps vs total executed steps.";
      nextAction.textContent = "Awaiting mission.";
      nextNote.textContent = "FORGE must say what should happen next when a task is partial or blocked.";
      resultPanel.textContent = "The verified result, evidence summary, and mission notes will appear here.";
      renderDiagnostics({});
      resetLiveProgress();
      workspaceSubtitle.textContent = "Ask in Arabic or English. FORGE replies naturally, and executes only when your request needs tools.";
      setListPlaceholder(planList, "No execution plan yet.");
      setListPlaceholder(stepList, "No steps executed yet.");
      promptBox.focus();
    });

    diagnosticsToggle.addEventListener("click", () => {
      const isHidden = diagnosticsPanel.classList.toggle("hidden");
      diagnosticsToggle.textContent = isHidden ? "Show technical details" : "Hide technical details";
    });

    promptBox.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendPrompt();
      }
    });

    window.setInterval(() => {
      fetch("/api/ping", { method: "POST" }).catch(() => {});
    }, 15000);
    window.setInterval(() => { if (currentUser) { loadWorkerTelemetry().catch(() => {}); } }, 2500);

    setListPlaceholder(planList, "No execution plan yet.");
    setListPlaceholder(stepList, "No steps executed yet.");
    setListPlaceholder(workerServices, "Worker telemetry loading...");
    updateConversationLayout();
    loadAuth()
      .then(async () => {
        if (currentUser) {
          await Promise.all([loadBootStatus(), loadWorkspace(), loadProviderKeys(), loadWorkerTelemetry(), loadAdmin()]);
        } else {
          setAuthStatus("Guest mode active. Sign in only for sync and private features.");
          await Promise.all([loadBootStatus(), loadWorkspace(), loadWorkerTelemetry()]);
        }
      })
      .catch((error) => {
        setAuthStatus(error.message);
      });
    promptBox.focus();
  </script>
</body>
</html>
"""


class DesktopRequestHandler(BaseHTTPRequestHandler):
    server: "ForgeDesktopHttpServer"

    def do_GET(self) -> None:
        self.server.touch()
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)

        if route in {"/", "/index.html"}:
            self._send_html(DESKTOP_HTML)
            return
        if route == "/api/auth/me":
            token = self._session_token()
            if not token:
                self._send_json(
                    {
                        "authenticated": False,
                        "manager_email": self.server.app_settings.manager_email,
                    }
                )
                return
            try:
                payload = self.server.portal.auth_me(token)
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(payload)
            return
        if route == "/api/auth/device/status":
            device_code = str(query.get("device_code", [""])[0]).strip()
            if not device_code:
                self._send_json({"error": "device_code is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                reply = self.server.portal.device_login_status(device_code)
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            headers = {}
            if reply.session_token:
                headers["Set-Cookie"] = self._session_cookie(reply.session_token)
            self._send_json(reply.payload, headers=headers or None)
            return
        if route == "/api/stream":
            user = self._current_user()
            prompt = str(query.get("prompt", [""])[0]).strip()
            if not prompt:
                self._send_json({"error": "Prompt is empty."}, status=HTTPStatus.BAD_REQUEST)
                return

            confirmed = str(query.get("confirmed", ["false"])[0]).strip().lower() in {"1", "true", "yes", "on"}
            dry_run = str(query.get("dry_run", ["false"])[0]).strip().lower() in {"1", "true", "yes", "on"}
            workspace_root = str(query.get("workspace_root", [""])[0]).strip() or None
            try:
                self._start_sse()
                for event in stream_prompt(
                    prompt,
                    confirmed=confirmed,
                    dry_run=dry_run,
                    workspace_root=workspace_root,
                    provider_secrets=self._runtime_provider_secrets(),
                ):
                    if event.get("type") == "done" and isinstance(event.get("payload"), dict):
                        if user is not None:
                            self._sync_remote_mission(user, event["payload"])
                    self._send_sse(event)
            except BrokenPipeError:
                return
            except ConnectionResetError:
                return
            except Exception as exc:
                try:
                    self._send_sse({"type": "error", "error": str(exc)})
                except Exception:
                    pass
            return
        if route == "/api/boot":
            try:
                status = boot_status_for_user(self._runtime_provider_secrets())
            except Exception as exc:
                log_exception("Boot endpoint failed", exc)
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            payload = {
                "providers": status.providers,
                "models_online": status.models_online,
                "summary": status.summary,
                "version": f"FORGE v{__version__}",
                "workspace_root": status.workspace_root,
                "artifact_root": status.artifact_root,
                "provider_setup": status.provider_setup,
            }
            self._send_json(payload)
            return
        if route == "/api/workspace":
            try:
                self._send_json(get_workspace_status())
            except Exception as exc:
                log_exception("Workspace status endpoint failed", exc)
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/workers":
            self._send_json({"workers": MissionOrchestrator.worker_snapshot()})
            return
        if route == "/api/user/keys":
            if self._require_user() is None:
                return
            try:
                payload = self.server.portal.list_user_keys(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            if not payload.get("providers"):
                payload["providers"] = supported_provider_names()
            self._send_json(payload)
            return
        if route == "/api/admin/overview":
            if self._require_admin() is None:
                return
            try:
                payload = self.server.portal.admin_overview(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            payload["workers"] = MissionOrchestrator.worker_snapshot()
            payload["local_approvals"] = MissionOrchestrator.approvals_snapshot()
            self._send_json(payload)
            return
        if route == "/api/admin/users":
            if self._require_admin() is None:
                return
            try:
                payload = self.server.portal.admin_users(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(payload)
            return
        if route == "/api/admin/approvals":
            if self._require_admin() is None:
                return
            try:
                payload = self.server.portal.admin_approvals(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(payload)
            return
        if route == "/api/admin/missions":
            if self._require_admin() is None:
                return
            try:
                payload = self.server.portal.admin_missions(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(payload)
            return
        if route == "/api/admin/key-health":
            if self._require_admin() is None:
                return
            try:
                payload = self.server.portal.admin_key_health(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(payload)
            return
        self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        self.server.touch()
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/auth/register":
            payload = self._read_json()
            try:
                reply = self.server.portal.register(
                    {
                        "email": str(payload.get("email", "")).strip(),
                        "password": str(payload.get("password", "")),
                        "display_name": str(payload.get("display_name", "")).strip(),
                    }
                )
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            headers = {}
            if reply.session_token:
                headers["Set-Cookie"] = self._session_cookie(reply.session_token)
            self._send_json(reply.payload, headers=headers or None)
            return
        if route == "/api/auth/login":
            payload = self._read_json()
            try:
                reply = self.server.portal.login(
                    {
                        "email": str(payload.get("email", "")).strip(),
                        "password": str(payload.get("password", "")),
                    }
                )
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            headers = {}
            if reply.session_token:
                headers["Set-Cookie"] = self._session_cookie(reply.session_token)
            self._send_json(reply.payload, headers=headers or None)
            return
        if route == "/api/auth/logout":
            token = self._session_token()
            if token:
                try:
                    self.server.portal.logout(token)
                except PortalApiError:
                    pass
            self._send_json(
                {"authenticated": False},
                headers={"Set-Cookie": self._session_cookie("", expire=True)},
            )
            return
        if route == "/api/auth/request-verification":
            if self._require_user() is None:
                return
            try:
                payload = self.server.portal.request_verification(self._session_token() or "")
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(payload)
            return
        if route == "/api/auth/verify-email":
            payload = self._read_json()
            try:
                response = self.server.portal.verify_email(
                    str(payload.get("token", "")).strip(),
                    session_token=self._session_token(),
                )
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(response)
            return
        if route == "/api/auth/request-password-reset":
            payload = self._read_json()
            try:
                response = self.server.portal.request_password_reset(str(payload.get("email", "")).strip())
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(response)
            return
        if route == "/api/auth/reset-password":
            payload = self._read_json()
            try:
                reply = self.server.portal.reset_password(
                    str(payload.get("token", "")).strip(),
                    str(payload.get("password", "")),
                )
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            headers = {}
            if reply.session_token:
                headers["Set-Cookie"] = self._session_cookie(reply.session_token)
            self._send_json(reply.payload, headers=headers or None)
            return
        if route == "/api/auth/device/start":
            payload = self._read_json()
            mode = str(payload.get("mode", "browser")).strip().lower() or "browser"
            if mode not in {"browser", "google"}:
                mode = "browser"
            try:
                response = self.server.portal.start_device_login(
                    {
                        "display_name": str(payload.get("display_name", "")).strip(),
                        "mode": mode,
                    }
                )
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            self._send_json(response)
            return
        if route == "/api/ping":
            self._send_json({"ok": True})
            return
        if route == "/api/demo/prepare":
            try:
                self._send_json(prepare_demo_workspace())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/user/keys":
            if self._require_user() is None:
                return
            payload = self._read_json()
            try:
                response = self.server.portal.save_user_key(self._session_token() or "", payload)
            except PortalApiError as exc:
                self._send_json(exc.payload, status=HTTPStatus(exc.status))
                return
            if not response.get("providers"):
                response["providers"] = supported_provider_names()
            self._send_json(response)
            return
        if route == "/api/workspace":
            payload = self._read_json()
            workspace_root = str(payload.get("workspace_root", "")).strip()
            if not workspace_root:
                self._send_json({"error": "Workspace path is empty."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = set_workspace_root(workspace_root)
            except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json(result)
            return
        if route == "/api/workspace/dialog":
            try:
                result = choose_workspace_root()
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json(result)
            return
        if route == "/api/chat":
            user = self._current_user()
            payload = self._read_json()
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                self._send_json({"error": "Prompt is empty."}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                result = operate_prompt(
                    prompt,
                    confirmed=bool(payload.get("confirmed")),
                    dry_run=bool(payload.get("dry_run")),
                    workspace_root=payload.get("workspace_root"),
                    provider_secrets=self._runtime_provider_secrets(),
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if user is not None:
                self._sync_remote_mission(user, result)
            self._send_json({"answer": result.get("answer"), "mode": "operator_only"})
            return
        if route == "/api/operate":
            user = self._current_user()
            payload = self._read_json()
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                self._send_json({"error": "Prompt is empty."}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                result = operate_prompt(
                    prompt,
                    confirmed=bool(payload.get("confirmed")),
                    dry_run=bool(payload.get("dry_run")),
                    workspace_root=payload.get("workspace_root"),
                    provider_secrets=self._runtime_provider_secrets(),
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if user is not None:
                self._sync_remote_mission(user, result)
            self._send_json(result)
            return

        self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        if morsel is None:
            return None
        value = morsel.value.strip()
        return value or None

    def _current_user(self) -> dict[str, object] | None:
        token = self._session_token()
        if not token:
            return None
        try:
            payload = self.server.portal.auth_me(token)
        except PortalApiError:
            return None
        if not payload.get("authenticated"):
            return None
        return payload.get("user")

    def _provider_secrets(self) -> dict[str, dict[str, str]]:
        token = self._session_token()
        if not token:
            return {}
        return self.server.portal.export_user_secrets(token)

    def _runtime_provider_secrets(self) -> dict[str, dict[str, str]] | None:
        if self._current_user() is None:
            return None
        return self._provider_secrets()

    def _require_user(self) -> dict[str, object] | None:
        user = self._current_user()
        if user is None:
            self._send_json({"error": "Login required."}, status=HTTPStatus.UNAUTHORIZED)
            return None
        return user

    def _require_admin(self) -> dict[str, object] | None:
        user = self._require_user()
        if user is None:
            return None
        if not bool(user.get("is_admin")):
            self._send_json({"error": "Admin access required."}, status=HTTPStatus.FORBIDDEN)
            return None
        return user

    def _sync_remote_mission(self, user: dict[str, object], result: dict[str, object]) -> None:
        token = self._session_token()
        if not token:
            return
        mission_id = str(result.get("mission_id", "")).strip()
        if not mission_id:
            return
        try:
            self.server.portal.sync_mission(
                token,
                {
                    "mission_id": mission_id,
                    "objective": str(result.get("objective", "")).strip(),
                    "status": str(result.get("validation_status", "")).strip() or "unknown",
                    "validation_status": str(result.get("validation_status", "")).strip() or "unknown",
                    "summary": str(result.get("result", "") or result.get("answer", "") or "No result."),
                    "workspace_root": str(result.get("workspace_root", "")).strip(),
                    "source": "desktop",
                },
            )
            pending = []
            for approval in MissionOrchestrator.approvals_snapshot():
                if str(approval.get("mission_id", "")).strip() != mission_id:
                    continue
                pending.append(
                    {
                        "approval_id": approval.get("approval_id"),
                        "mission_id": approval.get("mission_id"),
                        "step_id": approval.get("step_id"),
                        "approval_class": approval.get("approval_class"),
                        "status": approval.get("status"),
                        "summary": approval.get("summary"),
                        "request_excerpt": approval.get("request_excerpt"),
                        "source": "desktop",
                    }
                )
            if pending:
                self.server.portal.sync_approvals(token, {"approvals": pending})
        except PortalApiError as exc:
            log_exception("Portal mission sync failed", exc)

    @staticmethod
    def _session_cookie(token: str, *, expire: bool = False) -> str:
        parts = [SESSION_COOKIE_NAME + "=" + token, "Path=/", "HttpOnly", "SameSite=Lax"]
        if expire:
            parts.append("Max-Age=0")
        return "; ".join(parts)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _start_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _send_sse(self, payload: dict[str, object]) -> None:
        body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def _send_html(self, html: str, headers: dict[str, str] | None = None) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        payload: dict[str, object],
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


class ForgeDesktopHttpServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int) -> None:
        super().__init__((host, port), DesktopRequestHandler)
        self.app_settings = OperatorSettings(enable_memory=False)
        self.portal = PortalAccountClient(
            self.app_settings.portal_api_base_url,
            timeout_seconds=self.app_settings.portal_request_timeout_seconds,
        )

    def touch(self) -> None:
        pass

def launch_desktop(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> str:
    log_event(f"Launching desktop server host={host} port={port} open_browser={open_browser}")
    try:
        server = ForgeDesktopHttpServer(host, port)
    except Exception as exc:
        log_exception("Server bind failed", exc)
        raise

    address = f"http://{server.server_address[0]}:{server.server_address[1]}"
    log_event(f"Desktop server bound at {address}")

    if open_browser:
        threading.Timer(0.45, lambda: webbrowser.open(address, new=1)).start()
        log_event("Browser launch scheduled")

    try:
        server.serve_forever()
    except Exception as exc:
        log_exception("Desktop server runtime failed", exc)
        raise
    finally:
        log_event("Desktop server shutting down")
        server.server_close()
    return address

