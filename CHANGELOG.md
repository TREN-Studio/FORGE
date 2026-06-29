# Changelog

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
