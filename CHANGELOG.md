# Changelog

## v1.5.2 (2026-07-17)

### Added
- **Model Selector (Task 2 complete)**: `/api/models` endpoint collects ModelSpecs from every provider. UI dropdown in sidebar lets users pick a specific model per request. `model_hint` parameter flows through `_rank()` → `route()` → `route_stream()` → session → runtime → `/api/stream`. Router prioritizes hinted model when available.
- **Desktop Feature Parity E2E tests**: 16 unit tests covering Binary Guard (5), File Attachments (5), Model Selector (3), Mode Flow (1), Markdown Rendering (2) in `tests/unit/test_desktop_features.py`.
- **Full Arabic support**: FORGE now speaks Arabic by default on request.

### Changed
- **CHANGELOG.md**: v1.5.1 now includes full Desktop Feature Parity entries (Binary Guard, File Attachments, Model Selector, Mode Selector, Markdown, CLI Workspace, Boot Key Summary).
- **test_model_expansion.py**: Updated Groq/OpenRouter model assertions to match current live model lists.

## v1.5.1 (2026-07-16)

### Added
- **Trending Research skill**: Google Trends + Amazon BSR fetching in a new pluggable skill `trending-research`.
  - Fetches real-time Google Trends related queries and daily trending searches.
  - Scrapes Amazon Best Sellers Rank data for any product niche.
  - Caches results (5 min TTL) to avoid rate limits.
  - Falls back gracefully if one source fails.
- **Optional `research` extras**: `pip install forge-agent[research]` for pytrends + lxml dependencies.
- **Binary File Guard**: 16 binary extensions filtered from workspace scanning. `read_text` returns `""`, `read_excerpt` raises `ValueError`, `key_files` excludes binaries, `preview_text_edit` refuses binary edits.
- **File Attachments API**: `POST /api/upload` endpoint, `save_uploaded_attachment()` + `build_attachment_context()` in runtime. Upload button, paste from clipboard, drag-and-drop in Desktop UI. Text files merge into prompt context; images store as workspace references.
- **Model Selector**: `/api/models` endpoint collecting `ModelSpec` from every provider. UI dropdown with per-request `model_hint` parameter. Router respects `require_vision` and `model_hint` via `_rank()`. `ModelSpec.supports_vision` already in place.
- **Mode Selector**: Three modes in `stream_prompt()` — Chat (bypass planner + router, direct `session.stream_response`), Plan (build plan preview then stop), Build (full execution flow). Radio buttons in UI toggle between modes.
- **Markdown Rendering**: `renderMarkdownLite()` renders code blocks in `<pre class="forge-code-block">` and inline code in `<code class="forge-inline-code">`. Streaming uses raw `textContent`; final message renders full markdown. No external libraries.
- **CLI Workspace System**: `forge init` creates `.forge/` workspaces, `--workspace` flag at startup, `/workspace <path>` slash command to switch during session, auto-key bootstrap from `~/.forge/keys/`.
- **Boot Key Summary**: Desktop boot now counts and displays local keys from `~/.forge/keys/`.

### Changed
- **Site downloads page** updated from v1.1.8 → v1.5.1 with workspace system + auto-key messaging.
- **Groq provider**: Updated model list — added Qwen 3 32B, LLaMA 4 Scout; removed deprecated models (DeepSeek R1, Mixtral, Gemma 2).
- **OpenRouter provider**: Removed deprecated `:free` suffix from model IDs.
- **Router resilience**: Increased progressive attempt limit from 3 → 8 for better fallback coverage.

## v1.5.0 (2026-06-29)

### Added
- **Desktop UI Refinement**: Outfit + JetBrains Mono font family integration for polished typography.
- **Glassmorphic Card Aesthetics**: Modern frosted-glass design system across Desktop app.
- **Interactive Council Dashboard Toggle**: Real-time council trace visibility in Desktop UI.
- **Font family fallback chain**: System fonts with graceful degradation.

## v1.4.0 (2026-06-29)

### Added
- **Local Semantic Memory**: SQLite FTS5 virtual table indexing for full-text search across memories.
- **Multi-word matching**: BM25 relevance ranking for context-aware recall.
- **Memory recall API**: Exposed via `forge memory --show` with ranked results.
- **Memory persistence**: Conversations survive session restarts with vector-like search.

## v1.3.0 (2026-06-29)

### Added
- **Model Expansion**: DeepSeek R1 Distill (via Groq) and expanded OpenRouter free model catalog.
- **HuggingFace Serverless Provider**: Inference via HuggingFace Inference API for community models.
- **25 models online across 7 providers**: Ollama, Groq, Gemini, Nvidia, Cloudflare, Anthropic, OpenAI.
- **Smart fallback ordering**: Providers sorted by latency + success rate.

## v1.2.0 (2026-06-29)

- **Dynamic Multi-Agent Spawning**: Enabled FORGE to dynamically spawn, configure, and assign 20+ specialized agent roles (PythonSpecialist, DBConsultant, GitManager, SEOAuditor, WebScraper, etc.) tailored specifically to plan step requirements.
- **LLM-Powered Council & Workers**: Upgraded Research and Critic agents from rule-based to LLM-powered reasoning with graceful code-based fallbacks.
- **Agent Factory**: Added `AgentFactory` in `forge/brain/agent_factory.py` to extract agent roles from plan steps using domain-specific keyword mapping.
- **Dynamic Worker Execution**: Enabled worker runtime environments to execute dynamically resolved specialized tasks through child lanes.

## v1.1.9 (2026-06-29)

- **Persistent Identity & Conversation DNA**: Preserves FORGE's reasoning fingerprint, task context, decisions, and preferences across provider switches mid-conversation.
- **History Sanitization**: Sanitizes message history before sending to any provider to prevent assistant identity contamination.
- **Mid-Stream Delta Guard**: Intercepts and sanitizes streaming tokens in real time on CLI and Desktop.
- **Identity API Endpoint**: Added offline `/api/identity` endpoint for instant branding status check.
- **CLI Brand Anchor**: Added offline `forge identity` CLI command for instant verification.

## v1.1.8 (2026-05-03)

- Identity Guard + Fast Path: simple questions respond in under 1 second.
- FORGE consistently identifies as TREN Studio / Larbi Aboudi.
- Real Execution: creates files on Desktop and explicit local paths.
- Smart Routing + Progressive Timeout for fast, normal, and complex requests.
- Workspace safety avoids System32 as the default CLI workspace.
- Streaming status events appear while FORGE waits on slower routes.
