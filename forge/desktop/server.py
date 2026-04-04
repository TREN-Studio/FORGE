from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from forge.brain.orchestrator import MissionOrchestrator
from forge import __version__
from forge.desktop.diagnostics import log_event, log_exception
from forge.desktop.runtime import boot_status, operate_prompt, run_prompt


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

    .shell {
      position: relative;
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 24px;
      min-height: 100vh;
      padding: 28px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .sidebar {
      display: flex;
      flex-direction: column;
      gap: 18px;
      padding: 24px;
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
      grid-template-rows: auto auto 1fr auto;
      min-height: calc(100vh - 56px);
      overflow: hidden;
    }

    .workspace-header {
      padding: 26px 28px 18px;
      border-bottom: 1px solid var(--line);
    }

    .workspace-header h2 {
      margin: 0;
      font-size: 30px;
      letter-spacing: 0.02em;
    }

    .workspace-header p {
      margin: 10px 0 0;
      max-width: 820px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.6;
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
      padding: 24px 28px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .bubble {
      max-width: min(820px, 92%);
      padding: 18px 18px 16px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.03);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.02);
    }

    .bubble.user {
      align-self: flex-end;
      background: linear-gradient(180deg, rgba(255,107,26,0.18), rgba(255,107,26,0.07));
      border-color: rgba(255,107,26,0.28);
    }

    .bubble.assistant {
      align-self: flex-start;
      background: rgba(255,255,255,0.035);
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

    .composer {
      padding: 20px 28px 28px;
      border-top: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(8,8,8,0.02), rgba(8,8,8,0.36));
    }

    .composer-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 160px;
      gap: 16px;
    }

    textarea {
      width: 100%;
      min-height: 132px;
      resize: vertical;
      border-radius: 20px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.3);
      color: var(--text);
      padding: 18px 18px;
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

    .footnote {
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    @media (max-width: 1080px) {
      .shell {
        grid-template-columns: 1fr;
      }
      .workspace {
        min-height: auto;
      }
      .operator-deck,
      .operator-grid {
        grid-template-columns: 1fr;
      }
      .composer-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
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
          <input id="operator-mode" type="checkbox" checked>
          <span>Agent Mode</span>
        </label>
        <p class="footnote">
          Agent Mode is the default path. FORGE should plan, route, validate, and expose evidence instead of behaving like a plain chatbot.
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
    </aside>

    <main class="panel workspace">
      <header class="workspace-header">
        <h2>Operator Mission Console</h2>
        <p>
          FORGE runs on-device, chooses a live model path, executes through skills, and must prove what it did with visible steps, evidence, and validation.
        </p>
      </header>

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

      <section id="chat" class="chat">
        <article class="bubble assistant">
          <header>FORGE</header>
          <div class="body">Agent console online. Ask FORGE to inspect this computer, analyze the project, plan a workflow, or execute a verified task path.</div>
        </article>
        <article class="bubble assistant">
          <header>Status</header>
          <div id="result-panel" class="body">The verified result, evidence summary, and mission notes will appear here.</div>
        </article>
      </section>

      <section class="composer">
        <div class="composer-grid">
          <textarea id="prompt" placeholder="Give FORGE a real mission: inspect this computer, analyze this codebase, plan a release workflow, or produce a verified artifact..."></textarea>
          <div class="actions">
            <button id="send" type="button">Run Mission</button>
            <button id="clear" type="button">Reset</button>
          </div>
        </div>
        <div class="footnote">
          Press Ctrl+Enter to dispatch. Agent Mode returns objective, plan, validation status, step evidence, and the best next action.
        </div>
      </section>
    </main>
  </div>

  <script>
    const chat = document.getElementById("chat");
    const promptBox = document.getElementById("prompt");
    const sendButton = document.getElementById("send");
    const clearButton = document.getElementById("clear");
    const notes = document.getElementById("notes");
    const workerServices = document.getElementById("worker-services");
    const operatorMode = document.getElementById("operator-mode");
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

    function appendNote(line) {
      notes.textContent += "\\n" + line;
      notes.scrollTop = notes.scrollHeight;
    }

    function clearNode(node) {
      while (node.firstChild) {
        node.removeChild(node.firstChild);
      }
    }

    function addBubble(role, text) {
      const article = document.createElement("article");
      article.className = "bubble " + role;

      const header = document.createElement("header");
      header.textContent = role === "user" ? "You" : role === "error" ? "FORGE Error" : "FORGE";

      const body = document.createElement("div");
      body.className = "body";
      body.textContent = text;

      article.appendChild(header);
      article.appendChild(body);
      chat.appendChild(article);
      chat.scrollTop = chat.scrollHeight;
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
          "\\n" +
          (service.workers || []).map((worker) =>
            worker.lane_id + ": active=" + String(worker.active_jobs || 0) +
            ", queued=" + String(worker.queued_jobs || 0) +
            ", processed=" + String(worker.processed_jobs || 0)
          ).join("\\n");

        header.appendChild(name);
        header.appendChild(state);
        row.appendChild(header);
        row.appendChild(meta);
        workerServices.appendChild(row);
      });
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
      resultPanel.textContent = data.result || data.answer || "No result produced.";
      renderPlan(data.plan);
      renderSteps(data.step_results);
    }

    async function loadBootStatus() {
      try {
        const response = await fetch("/api/boot");
        const data = await response.json();
        document.getElementById("runtime-state").textContent = data.models_online > 0 ? "Ready" : "Limited";
        document.getElementById("providers").textContent = String(data.providers);
        document.getElementById("models").textContent = String(data.models_online);
        document.getElementById("version").textContent = data.version;
        appendNote(data.summary);
      } catch (error) {
        document.getElementById("runtime-state").textContent = "Failed";
        appendNote("Boot request failed: " + error.message);
      }
    }

    async function loadWorkerTelemetry() {
      try {
        const response = await fetch("/api/workers");
        const data = await response.json();
        renderWorkers(data);
      } catch (error) {
        setListPlaceholder(workerServices, "Worker telemetry unavailable: " + error.message);
      }
    }

    async function sendPrompt() {
      const prompt = promptBox.value.trim();
      if (!prompt || sendButton.disabled) return;

      addBubble("user", prompt);
      appendNote("Dispatching prompt to live runtime...");
      promptBox.value = "";
      sendButton.disabled = true;
      sendButton.textContent = "Running...";
      if (operatorMode.checked) {
        setMissionStatus("running");
      }

      try {
        const response = await fetch(operatorMode.checked ? "/api/operate" : "/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt,
            operator: operatorMode.checked
          })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Execution failed.");
        }
        if (operatorMode.checked) {
          renderOperatorResult(data);
          addBubble("assistant", data.answer || data.result || "No result produced.");
        } else {
          addBubble("assistant", data.answer);
        }
        appendNote("Response completed successfully.");
      } catch (error) {
        addBubble("error", error.message);
        if (operatorMode.checked) {
          setMissionStatus("failed");
        }
        appendNote("Execution failed: " + error.message);
      } finally {
        sendButton.disabled = false;
        sendButton.textContent = "Run Mission";
        promptBox.focus();
      }
    }

    sendButton.addEventListener("click", sendPrompt);
    clearButton.addEventListener("click", () => {
      chat.innerHTML = "";
      addBubble("assistant", "Mission console cleared. FORGE is ready for the next task.");
      objective.textContent = "No active objective.";
      objectiveNote.textContent = "Submit a serious task to generate a real operator mission.";
      setMissionStatus("idle");
      validationNote.textContent = "Validation status appears here after execution.";
      stepMetrics.textContent = "0 / 0";
      stepNote.textContent = "Completed steps vs total executed steps.";
      nextAction.textContent = "Awaiting mission.";
      nextNote.textContent = "FORGE must say what should happen next when a task is partial or blocked.";
      resultPanel.textContent = "The verified result, evidence summary, and mission notes will appear here.";
      setListPlaceholder(planList, "No execution plan yet.");
      setListPlaceholder(stepList, "No steps executed yet.");
    });

    promptBox.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.ctrlKey) {
        event.preventDefault();
        sendPrompt();
      }
    });

    window.setInterval(() => {
      fetch("/api/ping", { method: "POST" }).catch(() => {});
    }, 15000);
    window.setInterval(loadWorkerTelemetry, 2500);

    setListPlaceholder(planList, "No execution plan yet.");
    setListPlaceholder(stepList, "No steps executed yet.");
    setListPlaceholder(workerServices, "Worker telemetry loading...");
    loadBootStatus();
    loadWorkerTelemetry();
    promptBox.focus();
  </script>
</body>
</html>
"""


class DesktopRequestHandler(BaseHTTPRequestHandler):
    server: "ForgeDesktopHttpServer"

    def do_GET(self) -> None:
        self.server.touch()
        if self.path in {"/", "/index.html"}:
            self._send_html(DESKTOP_HTML)
            return
        if self.path == "/api/boot":
            try:
                status = boot_status()
            except Exception as exc:
                log_exception("Boot endpoint failed", exc)
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            payload = {
                "providers": status.providers,
                "models_online": status.models_online,
                "summary": status.summary,
                "version": f"FORGE v{__version__}",
            }
            self._send_json(payload)
            return
        if self.path == "/api/workers":
            self._send_json({"workers": MissionOrchestrator.worker_snapshot()})
            return
        self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        self.server.touch()
        if self.path == "/api/ping":
            self._send_json({"ok": True})
            return
        if self.path == "/api/chat":
            payload = self._read_json()
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                self._send_json({"error": "Prompt is empty."}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                answer = run_prompt(prompt, use_operator=bool(payload.get("operator")))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json({"answer": answer})
            return
        if self.path == "/api/operate":
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
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json(result)
            return

        self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ForgeDesktopHttpServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int) -> None:
        super().__init__((host, port), DesktopRequestHandler)

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
