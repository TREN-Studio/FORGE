# FORGE

**Free Open Reasoning and Generation Engine**

FORGE is an English-first, multilingual AI operator that routes across free and local models, chooses the best execution path for the task, and runs through a modular skill system built for real-world work.

[![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-orange.svg)](https://python.org)
[![Website](https://img.shields.io/badge/website-trenstudio.com%2FFORGE-orange.svg)](https://www.trenstudio.com/FORGE)

## Why FORGE

- English-first product surface with multilingual task support.
- Smart model routing across free providers and local runtimes.
- Skill-based execution brain with planning, safety, validation, and recovery.
- Persistent context and memory for long-running operator workflows.
- Safe artifact generation for reports, analysis outputs, and execution traces.

## Core Capabilities

### Universal Model Routing

FORGE connects to multiple providers and local runtimes, scores candidates for the active task, and selects the strongest available path.

#### How the Smart Selector Works (in 60 seconds)

Every request passes through the `ForgeRouter` (`forge/core/router.py`). Here is the decision chain, end to end:

1. **Classify** — the prompt is classified into a *speed class* (`fast` ≤6 words, `normal`, `complex` — multi-step verbs like "create … then run the tests") and a *task type* (code, math, research, creative, fast, reasoning, general).
2. **Score** — every registered model gets a real-time composite score:

   ```
   score = quality×0.40 + quota_remaining×0.30 + speed×0.20 + success_rate×0.10
   ```

   Quality is a per-model tier/tag heuristic; quota and success rate update *after every call*, so the router learns from live behavior.
3. **Affinity boost** — each task type has preferred providers/tags (e.g. CODE prefers `coding`/`instruct` models on groq/deepseek; REASONING prefers `r1`/`think`-tagged models) and its own latency-vs-quality weighting.
4. **Call with a progressive timeout** — the total budget (`fast` 8s / `normal` 15s / `complex` 30s) is split across up to 8 attempts with weights summing to 1.0, so the whole fallback chain mathematically *cannot* exceed the budget.
5. **Fail → fall back instantly** — on timeout, quota, or error the model is demoted (`SLOW`/`QUOTA` status drops its score to 0) and the next-ranked candidate is tried. On success, latency and reliability stats are recorded back.
6. **Persist the choice** — the final provider, every attempted hop, and per-attempt timeouts land in `routing_telemetry` on the response, so any UI can show *why* a model won.

The result, proven live (see below): even when 4 of 7 configured providers fail, **zero requests are lost** — the chain reroutes to the first healthy model in under a second.

#### Live Multi-Provider Proof (E2E)

`tools/e2e_routing_demo.py` sends the *same* request through every keyed provider and then lets the Smart Selector choose freely. Latest verified run (2026-09-10, 7 providers keyed):

| Provider | Direct result | What actually happened |
|---|---|---|
| groq / `gpt-oss-120b` | ✅ 593 ms, 82 tok | answered directly |
| nvidia / `nemotron-3-super-120b-a12b` | ✅ 2,515 ms, 193 tok | answered directly |
| gemini | 🔁 recovered | provider 403 → router fell back to groq, ✅ 1,875 ms |
| openrouter | 🔁 recovered | credits exhausted (402) → fell back to groq, ✅ 937 ms |
| cloudflare | 🔁 recovered | auth 401 → fell back to groq, ✅ 953 ms |
| openai | 🔁 recovered | rate limit → fell back to groq, ✅ 1,187 ms |
| ollama | 🔁 recovered | not running locally → fell back to groq |

**7/7 requests answered. 0 lost.** The full JSON report with per-attempt telemetry is saved to `.forge_artifacts/e2e_routing_report.json` on every run.

#### Run the Demo Yourself

```bash
# 1. Add at least one provider key (free Groq key: https://console.groq.com)
mkdir -p ~/.forge/keys
echo "gsk_..." > ~/.forge/keys/groq        # one file per provider

# 2. Run the same request through every keyed provider + the free-choice router
python tools/e2e_routing_demo.py
```

Or from Python:

```python
import asyncio
from forge.core.router import ForgeRouter
from forge.core.models import Message
from forge.providers.groq import GroqProvider

async def demo():
    router = ForgeRouter()
    router.register(GroqProvider())          # add more providers freely
    resp = await router.route(
        [Message(role="user", content="Summarize multi-provider routing in one sentence.")],
        timeout=15.0,
    )
    print(resp.provider, resp.model_id, resp.routing_telemetry)

asyncio.run(demo())
```

#### Currently Supported Providers (12)

| Provider | Key file / env | Notes |
|---|---|---|
| groq | `~/.forge/keys/groq` / `GROQ_API_KEY` | fastest free tier; gpt-oss-120b / qwen3.6 |
| nvidia | `~/.forge/keys/nvidia` / `NVIDIA_API_KEY` | NIM catalog: nemotron, deepseek-v4, kimi |
| gemini | `~/.forge/keys/gemini` / `GEMINI_API_KEY` | |
| openai | `~/.forge/keys/openai` / `OPENAI_API_KEY` | |
| openrouter | `~/.forge/keys/openrouter` / `OPENROUTER_API_KEY` | |
| anthropic | `~/.forge/keys/anthropic` / `ANTHROPIC_API_KEY` | |
| mistral | `~/.forge/keys/mistral` / `MISTRAL_API_KEY` | |
| together | `~/.forge/keys/together` / `TOGETHER_API_KEY` | |
| deepseek | `~/.forge/keys/deepseek` / `DEEPSEEK_API_KEY` | |
| cloudflare | `~/.forge/keys/cloudflare` / `CLOUDFLARE_API_TOKEN` | Workers AI |
| huggingface | `~/.forge/keys/huggingface` / `HF_TOKEN` | serverless inference |
| ollama | — | local models, no key needed |

Any subset works — FORGE degrades gracefully to whatever you have keyed.

### Skill-Based Operator Brain

The operator is split into explicit layers:

1. Intent resolution
2. Structured planning
3. Skill routing
4. Safety guard
5. Execution runtime
6. Validation
7. Recovery and fallback
8. Response composition

### Grounded Local Execution

FORGE can inspect a workspace, read files safely, analyze the codebase with evidence, and write output artifacts without mutating source files.

## Architecture

```text
forge/
  brain/         Core orchestration brain
  core/          Provider routing, quotas, discovery, session runtime
  memory/        Session and persistent context
  providers/     Provider adapters
  recovery/      Retry and fallback handling
  safety/        Risk policy and confirmation logic
  skills/        Skill contracts, registry, loader, router, runtime
  skills_catalog/Pluggable skill folders
  tools/         Safe local execution tools
  validation/    Output and completion validation
```

## Desktop Feature Parity (v1.5.1)

FORGE Desktop now includes five tightly-integrated features for a professional desktop experience:

### Workspace System (CLI + Desktop)
Every session ties to a folder. FORGE understands your project files, structure, and codebase — and operates within it. Use `forge init` to create a workspace or point the desktop app at any folder.

### Binary File Guard
Non-text files (`.png`, `.jpg`, `.pdf`, `.zip`, `.exe`, etc.) are safely excluded from content reading. Displayed in file tree for reference only — never scanned, never sent to a model.

### File & Image Attachment
Upload files via button, paste from clipboard, or drag-and-drop. Text files (`.txt`, `.md`, `.csv`, `.json`) get injected into the prompt context. Images are stored as workspace references (vision support coming in a future sprint).

### Model Selector
Choose a specific model from the dropdown, or leave it on "Auto" for FORGE's Smart Selector to pick the best available. The router respects your choice with hard override.

### Mode Selector — Chat / Plan / Build
- **Chat**: Direct conversation, no planning, no tool execution. Fast responses.
- **Plan**: Builds and displays the execution plan — stops before executing. Review and confirm.
- **Build**: Full autonomous execution. FORGE plans, executes, validates, and responds.

### Local Markdown Rendering
Code blocks and inline code render beautifully in the chat UI — zero external libraries, works offline.

Every skill is self-contained in its own folder:

```text
skills_catalog/<skill-name>/
  SKILL.md
  schema.json        # optional
  executor.py        # optional
```

This makes new skills pluggable without rewriting the core brain.

## Install

```bash
pip install forge-agent==1.5.2
forge --version  # FORGE 1.5.2
```

### What's New in v1.5.1

- **Desktop Feature Parity**: Binary guard, file attachment, model selector, mode selector (Chat/Plan/Build), markdown rendering.
- **Workspace System**: `forge init`, `--workspace` flag, `/workspace` slash command. Every session is tied to a folder.
- **Auto-Key Bootstrap**: Keys load automatically from `~/.forge/keys/` and environment variables — no signup needed.
- **Binary File Protection**: Non-text files excluded from scanning, reading, and editing.
- **File Attachments**: Upload, paste, or drag-and-drop files. Text content injected as context.

## Quick Start

```bash
forge --version
forge init                    # Create a workspace in the current folder
forge start                   # Start an interactive session
forge start -w ./my-project   # Start with a specific workspace folder
```

Inside a session:
```
/workspace                        # Show current workspace path
/workspace ~/Documents            # Switch to a different folder
```

Current CLI package release: `1.5.2` on PyPI.

## Python API

```python
import forge

result = forge.operate("Analyze this project and save a summary file")
print(result.result)
```

## Safety Model

- Untrusted external skills do not run automatically.
- Medium-risk actions can be forced into dry-run mode.
- High-risk actions require confirmation.
- Validation runs before success is reported.
- FORGE never claims execution without evidence.

## Status

Current foundation includes:

- model routing and provider registry
- quota management and model discovery
- skill registry and skill router
- skill governance metadata, precondition checks, and gated Tier 4 routing
- live desktop execution streaming with visible plan and step progress
- safety, validation, and recovery layers
- grounded workspace analysis and file reading skills

## Public Releases

- Current public release line: `1.5.2`.
- The primary public user path is the Windows Desktop download from the official site and GitHub Release.
- PyPI remains the developer CLI path: https://pypi.org/project/forge-agent/1.5.2/
- GitHub's latest stable release must also resolve to `v1.5.2`.
- The canonical public release record is the GitHub Release for the matching tag: https://github.com/TREN-Studio/FORGE/releases/tag/v1.5.2
- The PyPI publishing workflow in `.github/workflows/publish-pypi.yml` builds the wheel and source distribution, publishes to PyPI through Trusted Publisher, and attaches `dist/*` to the GitHub Release.
- The Windows release workflow in `.github/workflows/release_forge_windows.yml` builds desktop installer and portable assets for the current release line.
- The supported desktop build entrypoint is `python tools/build_windows_desktop.py`; that script is the source of truth for orchestration and invokes the portable `FORGE-Desktop.spec`.
- Release packaging runs through `python tools/package_release_assets.py`, which writes release assets under `release-assets/` only. It does not publish or sync binary files into `site/downloads/`.
- The public downloads page at `site/downloads/index.html` reads `release-manifest.json`, puts Windows first, and keeps PyPI/source links under the developer section.
- `python tools/verify_release_public_assets.py --manifest release-assets/release-manifest.json` verifies version, size, SHA256, and GitHub Release presence. Add `--require-mirror` only after Hostinger has byte-identical mirrored assets.
- `tools/deploy_hostinger_site.py` deploys the downloads page, portal, and release manifest only; the TREN Studio root page owns `https://www.trenstudio.com/FORGE/`.
- Legacy desktop spec variants were removed. `FORGE-Desktop.spec` is the only supported PyInstaller spec.
- If `WINDOWS_PFX_BASE64` and `WINDOWS_PFX_PASSWORD` are configured in GitHub Secrets, the workflow signs both artifacts before publishing the GitHub Release.
- Until code signing is configured, Windows SmartScreen and local execution reputation checks can still block downloaded installers.

## Roadmap

1. Add guarded file editing and patch execution
2. Add web research and publishing skills
3. Add audit logs and evidence snapshots for every action
4. Add richer test coverage and benchmark suites

## Contributing

FORGE is designed as an open-source operator platform. Contributions should preserve:

- modular contracts
- safety-by-default behavior
- grounded execution
- production-oriented output quality

## License

MIT

## Links

- Website: https://www.trenstudio.com/FORGE
- Downloads: https://www.trenstudio.com/FORGE/downloads/
- Organization: https://github.com/TREN-Studio
- Repository: https://github.com/TREN-Studio/FORGE

## Production Deployment

FORGE keeps its public downloads page and portal bundle inside [`site/`](site). The production page at `https://www.trenstudio.com/FORGE/` remains the TREN Studio project page; the FORGE download interface lives at `https://www.trenstudio.com/FORGE/downloads/` and is deployed from `site/downloads/index.html`.

GitHub Release is the canonical release record. PyPI is the recommended install path for the current public Python package. Hostinger may serve an official mirror under `https://www.trenstudio.com/FORGE/downloads/`, but only when the files are copied from the same CI-built release assets and pass SHA256, file size, version, and byte-identity verification.

The current public download set is:

- `https://pypi.org/project/forge-agent/1.5.2/`
- https://pypi.org/project/forge-agent/1.5.2/ (PyPI)
- https://github.com/TREN-Studio/FORGE/releases/tag/v1.5.2 (GitHub Release)

No official-site binary mirror is published for the current release.

### Auto-Deploy Pipeline

GitHub Actions workflow: [`.github/workflows/deploy_forge_site.yml`](.github/workflows/deploy_forge_site.yml)

Deployment script: [`tools/deploy_hostinger_site.py`](tools/deploy_hostinger_site.py). It preserves the remote `/FORGE/index.html` root page unless this repository explicitly adds a root `site/index.html`.

Deploy guard: the script refuses to deploy `site/index.html` to `/FORGE/index.html` unless `--allow-root-index-deploy` or `FORGE_ALLOW_ROOT_INDEX_DEPLOY=1` is provided. Normal downloads/portal deploys also compare the remote `/FORGE/index.html` hash before and after upload and fail if it changes.

Route verification: [`tools/verify_forge_public_routes.py`](tools/verify_forge_public_routes.py) checks that `/FORGE/` is still the original project page, `/FORGE/downloads/` is still the downloads page, and the public `release-manifest.json` matches the expected release manifest.

Required GitHub repository secrets:

- `HOSTINGER_HOST`
- `HOSTINGER_PORT`
- `HOSTINGER_USERNAME`
- `HOSTINGER_PASSWORD`
- `HOSTINGER_REMOTE_ROOT`

Recommended `HOSTINGER_REMOTE_ROOT` value:

```text
domains/trenstudio.com/public_html/FORGE
```

Manual local deploy remains available:

```bash
python tools/deploy_hostinger_site.py
```
