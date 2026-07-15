# Changelog

## v1.5.1 (2026-07-16)

### Added
- **Trending Research skill**: Google Trends + Amazon BSR fetching in a new pluggable skill `trending-research`.
  - Fetches real-time Google Trends related queries and daily trending searches.
  - Scrapes Amazon Best Sellers Rank data for any product niche.
  - Caches results (5 min TTL) to avoid rate limits.
  - Falls back gracefully if one source fails.
- **Optional `research` extras**: `pip install forge-agent[research]` for pytrends + lxml dependencies.

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
