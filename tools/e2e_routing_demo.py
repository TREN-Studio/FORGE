"""
FORGE Multi-Provider E2E Routing Test
=====================================
Roadmap Phase 1 task: run the SAME request through several live providers
and compare results end-to-end.

Reads keys from ~/.forge/keys/<provider> (FORGE's native mechanism).
Only providers with keys present are tested. Results are saved to
.forge_artifacts/e2e_routing_report.json for the portfolio case study.

Run:
    python tools/e2e_routing_demo.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from forge.core.models import Message, TaskType
from forge.core.router import ForgeRouter
from forge.providers.registry import iter_provider_classes

REPORT_PATH = Path(".forge_artifacts") / "e2e_routing_report.json"

# The identical request sent through every available provider.
UNIFIED_PROMPT = (
    "Summarize in exactly one sentence why multi-provider routing "
    "makes an AI agent more reliable."
)


async def run_single_provider(router: ForgeRouter, provider_name: str, model_id: str | None) -> dict:
    """Send UNIFIED_PROMPT pinned to one provider via model_hint."""
    started = time.monotonic()
    try:
        response = await router.route(
            [Message(role="user", content=UNIFIED_PROMPT)],
            task_type=TaskType.GENERAL,
            max_tokens=200,
            temperature=0.3,
            timeout=30.0,
            model_hint=model_id,
        )
        elapsed = time.monotonic() - started
        telemetry = response.routing_telemetry or {}
        attempts = telemetry.get("attempts", [])
        return {
            "provider": response.provider,
            "model": response.model_id,
            "status": "success",
            "latency_ms": round(elapsed * 1000, 1),
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "fallback_count": telemetry.get("fallback_count", 0),
            "attempted": [f"{a.get('provider')}/{a.get('model')}" for a in attempts],
            "response_excerpt": (response.content or "")[:220],
        }
    except Exception as exc:
        elapsed = time.monotonic() - started
        return {
            "provider": provider_name,
            "model": model_id,
            "status": "error",
            "latency_ms": round(elapsed * 1000, 1),
            "error": str(exc)[:300],
        }


async def probe_live_model(router: ForgeRouter, provider_name: str) -> str | None:
    """Find the first model on this provider that actually answers, by probing
    the top-ranked candidates with a tiny request (max 2 probes)."""
    provider = router.get_provider(provider_name)
    if provider is None:
        return None
    candidates = [spec.id for spec in provider.list_models()[:2]]
    for model_id in candidates:
        try:
            await router.route(
                [Message(role="user", content="ping")],
                task_type=TaskType.GENERAL,
                max_tokens=16,
                temperature=0.0,
                timeout=20.0,
                model_hint=model_id,
            )
            return model_id
        except Exception:
            continue
    return None


async def main() -> None:
    router = ForgeRouter()

    # Register every provider class that has a key available.
    keyed: list[tuple[str, str | None]] = []  # (provider_name, live_model_id)
    for cls in iter_provider_classes():
        try:
            provider = cls()
        except Exception:
            continue
        if not provider.is_available:
            continue
        router.register(provider)
        live_model = await probe_live_model(router, provider.name)
        keyed.append((provider.name, live_model))

    print(f"Providers with keys available: {[name for name, _ in keyed]}")

    # Same request through each keyed provider (pinned to its live model).
    results = await asyncio.gather(
        *(run_single_provider(router, name, model) for name, model in keyed)
    )

    # Finally, let the Smart Selector pick freely (no hint) — the headline demo.
    selector_started = time.monotonic()
    selector_result: dict = {"provider": "router", "model": None, "status": "error", "error": "not run"}
    try:
        response = await router.route(
            [Message(role="user", content=UNIFIED_PROMPT)],
            task_type=TaskType.GENERAL,
            max_tokens=200,
            temperature=0.3,
            timeout=30.0,
        )
        selector_result = {
            "provider": response.provider,
            "model": response.model_id,
            "status": "success",
            "latency_ms": round((time.monotonic() - selector_started) * 1000, 1),
            "output_tokens": response.output_tokens,
            "response_excerpt": (response.content or "")[:220],
        }
    except Exception as exc:
        selector_result = {
            "provider": "router",
            "status": "error",
            "error": str(exc)[:300],
            "latency_ms": round((time.monotonic() - selector_started) * 1000, 1),
        }

    report = {
        "unified_prompt": UNIFIED_PROMPT,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "providers_tested": [name for name, _ in keyed],
        "per_provider_results": results,
        "smart_selector_result": selector_result,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nReport saved to {REPORT_PATH}")
    print("\n=== SAME REQUEST, EVERY PROVIDER ===")
    for r in results:
        if r["status"] == "success":
            print(f"[OK] {r['provider']}/{r['model']}  {r['latency_ms']}ms  {r['output_tokens']} tok")
            print(f"     {r['response_excerpt'][:120]}")
        else:
            print(f"[ERR] {r['provider']}: {r.get('error', '')[:140]}")
    print("\n=== SMART SELECTOR (free choice) ===")
    if selector_result["status"] == "success":
        print(f"[OK] {selector_result['provider']}/{selector_result['model']}  {selector_result['latency_ms']}ms")
        print(f"     {selector_result['response_excerpt'][:120]}")
    else:
        print(f"[ERR] {selector_result.get('error', '')[:140]}")


if __name__ == "__main__":
    asyncio.run(main())
