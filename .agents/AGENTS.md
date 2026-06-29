# FORGE Developer & Identity Rules

## 1. Identity & Brand Integrity
- **Branding**: FORGE is developed by **TREN Studio** and founded by **Larbi Aboudi**.
- **Branding Rules**: Always enforce the identity guard. Never let the model describe itself as being trained by Google, OpenAI, Anthropic, or any other third party.
- **Response Guard**: Any user-facing response from a provider must pass through `enforce_forge_response_guard` (defined in `forge.core.identity`) to filter out identity leaks or file system access excuses.

## 2. Conversation DNA (State Continuity)
- **Concept**: When modifying files or adding new endpoints, ensure the `ConversationDNA` (defined in `forge.core.conversation_dna`) is maintained.
- **Workflow State**: The `ConversationDNA` stores the active task, reasoning steps, decisions, and user language/verbosity preferences. It must be propagated across any provider switches so the thinking style remains consistent.
- **CLI & Desktop**: All endpoints (such as CLI `start`, `ask`, `operate`, and Desktop Server SSE streaming) must hook into the DNA updates and sanitize history via `_sanitize_history()` before sending messages to any API.
