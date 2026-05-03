from __future__ import annotations

import re


FORGE_IDENTITY_RESPONSE = "Developed by TREN Studio. Founded by Larbi Aboudi."
FORGE_FILE_CAPABILITY_RESPONSE = (
    "Yes. I can create, read, and edit files inside your selected FORGE workspace. "
    "Give me the file path and the content you want, and I will create it and verify it exists."
)
FORGE_GREETING_RESPONSE = "Hi! I'm FORGE. What do you want me to do?"
FORGE_TEST_RESPONSE = "FORGE is running. Give me a task."

FORGE_IDENTITY_SYSTEM_INSTRUCTION = f"""
Identity rules:
- You are FORGE.
- You are not OpenAI, Google, Anthropic, or any external model brand.
- Never say you are a language model trained by any company.
- Never claim FORGE was created, developed, trained, or founded by any entity except TREN Studio / Larbi Aboudi.
- If asked who created, built, made, trained, owns, developed, or founded you, answer exactly: "{FORGE_IDENTITY_RESPONSE}"
- If asked whether you are from OpenAI, Google, Anthropic, Gemini, Claude, ChatGPT, or another model/provider, answer exactly: "{FORGE_IDENTITY_RESPONSE}"
- You can create, read, and edit files inside the selected FORGE workspace.
- Never say you cannot access the file system when the user is asking about FORGE workspace actions.
"""

_IDENTITY_VENDOR_WORDS = {
    "openai",
    "google",
    "anthropic",
    "gemini",
    "claude",
    "chatgpt",
    "gpt",
    "bard",
    "forged",
    "labs",
}

_IDENTITY_TARGET_WORDS = {
    "you",
    "u",
    "your",
    "yourself",
    "forge",
    "model",
    "bot",
    "assistant",
    "ai",
    "فورج",
    "ك",
}

_IDENTITY_QUESTION_WORDS = {
    "who",
    "whos",
    "whose",
    "ho",
    "how",
    "what",
    "which",
    "where",
    "are",
    "is",
    "من",
    "ما",
    "هل",
}


def asks_identity(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    tokens = [token for token in re.split(r"[^a-z0-9\u0600-\u06ff]+", normalized) if token]
    token_set = set(tokens)

    creator_word = any(
        token.startswith(
            (
                "creat",
                "made",
                "make",
                "built",
                "build",
                "develop",
                "found",
                "own",
                "train",
                "صنع",
                "طور",
                "بنا",
                "مؤسس",
            )
        )
        for token in tokens
    )
    asks_about_target = bool(token_set & _IDENTITY_QUESTION_WORDS) and bool(token_set & _IDENTITY_TARGET_WORDS)
    if asks_about_target and creator_word:
        return True

    if {"what", "are", "you"}.issubset(token_set):
        return True
    if {"what", "is", "forge"}.issubset(token_set):
        return True
    if {"which", "model"}.issubset(token_set) and bool(token_set & _IDENTITY_TARGET_WORDS):
        return True
    if {"are", "you"}.issubset(token_set) and bool(token_set & _IDENTITY_VENDOR_WORDS):
        return True
    if {"from", "openai"}.issubset(token_set) or {"from", "google"}.issubset(token_set) or {"from", "anthropic"}.issubset(token_set):
        return True
    if "trained" in token_set and bool(token_set & _IDENTITY_TARGET_WORDS):
        return True

    identity_phrases = (
        "who made you",
        "who built you",
        "who developed you",
        "who created you",
        "who trained you",
        "who is your creator",
        "who founded",
        "who owns you",
        "how creat you",
        "how created you",
        "how were you created",
        "are you from openai",
        "are you from google",
        "are you from anthropic",
        "are you openai",
        "are you google",
        "are you anthropic",
        "are you chatgpt",
        "are you gemini",
        "are you claude",
        "are you a language model",
        "what are you",
        "what model are you",
        "which model are you",
        "من طورك",
        "من صنعك",
        "من بناك",
        "من المؤسس",
    )
    return any(phrase in normalized for phrase in identity_phrases)


def asks_file_capability(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    tokens = [token for token in re.split(r"[^a-z0-9\u0600-\u06ff]+", normalized) if token]
    token_set = set(tokens)
    if not token_set:
        return False

    asks_ability = bool(token_set & {"can", "could", "will", "would", "able", "do", "does"})
    target_is_forge = bool(token_set & {"you", "u", "forge", "assistant", "agent"})
    file_words = bool(token_set & {"file", "files", "txt", "document", "report", "folder", "directory"})
    create_words = any(token.startswith(("creat", "write", "make", "save", "edit", "modify")) for token in tokens)
    local_words = bool(token_set & {"pc", "computer", "workspace", "local", "machine", "desktop"})

    if asks_ability and target_is_forge and file_words and (create_words or local_words):
        return True

    capability_phrases = (
        "can you create a file",
        "can you create files",
        "can you create a file on my pc",
        "can you write a file",
        "can you save a file",
        "can forge create a file",
        "are you able to create files",
    )
    return any(phrase in normalized for phrase in capability_phrases)


def _missing_single_file_content_response(text: str) -> str | None:
    raw_text = str(text or "").strip()
    normalized = raw_text.lower()
    tokens = [token for token in re.split(r"[^a-z0-9\u0600-\u06ff.\\/-]+", normalized) if token]
    if len(tokens) > 14:
        return None
    if not any(token.startswith(("creat", "write", "make", "save")) for token in tokens):
        return None
    if any(marker in normalized for marker in (" with content ", " content ", " containing ", " text ", " says ", "```")):
        return None
    if re.search(r"\b(?:with\s+)?(?:content|text)\s*:", normalized):
        return None
    if any(token in {"project", "app", "tests", "analyze", "analyse", "read", "report"} for token in tokens):
        return None

    named_match = re.search(
        r"\b(?:named|called)\s+([^\s`\"']+\.(?:txt|md|json|py|csv|html|css|js|ts|yml|yaml))\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    file_matches = re.findall(
        r"\b[^\s`\"']+\.(?:txt|md|json|py|csv|html|css|js|ts|yml|yaml)\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if named_match:
        target = named_match.group(1).strip()
    elif len(file_matches) == 1:
        target = file_matches[0].strip()
    else:
        return None
    return (
        f"Yes. What content should I put in `{target}`? "
        f"You can say: create `{target}` with content hello forge."
    )


def instant_response(text: str) -> str | None:
    """Return a local answer for prompts that must never wait on a provider."""
    normalized = str(text or "").strip().lower()
    if not normalized:
        return None

    if asks_identity(normalized):
        return FORGE_IDENTITY_RESPONSE
    if asks_file_capability(normalized):
        return FORGE_FILE_CAPABILITY_RESPONSE
    missing_content = _missing_single_file_content_response(text)
    if missing_content is not None:
        return missing_content

    cleaned = normalized.strip(" \t\r\n?!.,:;\"'")
    short_tokens = [token for token in re.split(r"[^a-z0-9\u0600-\u06ff]+", cleaned) if token]
    if len(short_tokens) > 4:
        return None

    if cleaned in {"hi", "hello", "hey", "yo", "salam", "سلام", "اهلا", "أهلا"}:
        return FORGE_GREETING_RESPONSE
    if cleaned in {"test", "ping", "status?", "status"}:
        return FORGE_TEST_RESPONSE
    return None


def enforce_identity_guard(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    lowered = value.lower()

    leak_patterns = (
        r"\bi\s*(?:am|'m)\s+(?:a\s+)?(?:large\s+)?language model\b",
        r"\btrained by\b",
        r"\bcreated by\b.*\b(openai|google|anthropic|gemini|forged labs)\b",
        r"\bdeveloped by\b.*\b(openai|google|anthropic|gemini|forged labs)\b",
        r"\bi\s*(?:am|'m)\s+.*\b(openai|google|anthropic|gemini|claude|chatgpt)\b",
        r"\bfrom\s+(openai|google|anthropic|gemini)\b",
        r"\bforged labs\b",
    )
    if any(re.search(pattern, lowered, flags=re.IGNORECASE | re.DOTALL) for pattern in leak_patterns):
        return FORGE_IDENTITY_RESPONSE
    return value


def enforce_agent_capability_guard(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    lowered = value.lower()
    refusal_patterns = (
        r"\bi\s*(?:can'?t|cannot)\s+(?:directly\s+)?(?:create|write|edit|save)\s+files?\b",
        r"\bi\s*(?:do not|don't)\s+have\s+access\s+to\s+(?:your\s+)?(?:local\s+)?file\s+system\b",
        r"\bi\s*(?:can'?t|cannot)\s+access\s+(?:your\s+)?(?:local\s+)?file\s+system\b",
        r"\bi\s*(?:can'?t|cannot)\s+(?:directly\s+)?(?:access|modify)\s+files?\s+on\s+your\s+(?:computer|pc|machine)\b",
    )
    if any(re.search(pattern, lowered, flags=re.IGNORECASE | re.DOTALL) for pattern in refusal_patterns):
        return FORGE_FILE_CAPABILITY_RESPONSE
    return value


def enforce_forge_response_guard(text: str) -> str:
    return enforce_agent_capability_guard(enforce_identity_guard(text))
