"""
FORGE Quota Guardian
=====================
Tracks provider free-tier usage and keeps the router away from exhausted lanes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("forge.quota")


FREE_TIER_LIMITS: dict[str, dict] = {
    "groq": {
        "tokens_per_day": 500_000,
        "requests_per_day": 14_400,
        "tokens_per_minute": 6_000,
        "resets": "daily_utc_midnight",
        "free_models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
    },
    "gemini": {
        "tokens_per_day": 1_000_000,
        "requests_per_day": 1_500,
        "requests_per_minute": 15,
        "resets": "daily_utc_midnight",
        "free_models": [
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
        ],
    },
    "mistral": {
        "tokens_per_month": 1_000_000,
        "requests_per_month": 10_000,
        "resets": "monthly",
        "free_models": [
            "mistral-small-latest",
            "open-mistral-7b",
            "open-mixtral-8x7b",
        ],
    },
    "deepseek": {
        "tokens_per_day": 500_000,
        "requests_per_day": 50,
        "resets": "daily_utc_midnight",
        "free_models": [
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    },
    "together": {
        "credits_usd": 5.0,
        "resets": "one_time",
        "free_models": [
            "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
    },
    "openrouter": {
        "tokens_per_day": 200_000,
        "resets": "daily_utc_midnight",
        "free_models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "qwen/qwen-2.5-72b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "microsoft/phi-4:free",
        ],
    },
    "ollama": {
        "tokens_per_day": 0,
        "requests_per_day": 0,
        "resets": "never",
        "free_models": ["*"],
        "note": "Local, requires a downloaded model",
    },
    "huggingface": {
        "requests_per_day": 1_000,
        "resets": "daily_utc_midnight",
        "free_models": [
            "meta-llama/Meta-Llama-3.1-70B-Instruct",
            "mistralai/Mistral-7B-Instruct-v0.3",
            "Qwen/Qwen2.5-72B-Instruct",
        ],
    },
    "nvidia": {
        "credits": 1_000,
        "resets": "one_time",
        "free_models": [
            "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen2.5-72b-instruct",
            "mistralai/mixtral-8x22b-instruct-v0.1",
        ],
    },
}


@dataclass
class ProviderQuota:
    provider: str
    tokens_used: int = 0
    tokens_limit: int = 0
    requests_used: int = 0
    requests_limit: int = 0
    reset_policy: str = "daily_utc_midnight"
    next_reset: float = field(default_factory=time.time)
    exhausted: bool = False
    last_checked: float = field(default_factory=time.time)

    @property
    def tokens_remaining(self) -> int:
        if self.tokens_limit == 0:
            return 999_999_999
        return max(0, self.tokens_limit - self.tokens_used)

    @property
    def requests_remaining(self) -> int:
        if self.requests_limit == 0:
            return 999_999_999
        return max(0, self.requests_limit - self.requests_used)

    @property
    def utilisation(self) -> float:
        if self.tokens_limit == 0:
            return 0.0
        return min(1.0, self.tokens_used / self.tokens_limit)

    def should_reset(self) -> bool:
        return self.reset_policy != "never" and time.time() >= self.next_reset

    def do_reset(self) -> None:
        self.tokens_used = 0
        self.requests_used = 0
        self.exhausted = False
        self.next_reset = self._next_reset_time()
        logger.info(f"Quota reset for '{self.provider}'")

    def _next_reset_time(self) -> float:
        now = datetime.now(timezone.utc)
        if self.reset_policy == "daily_utc_midnight":
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return tomorrow.timestamp()
        if self.reset_policy == "monthly":
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return next_month.timestamp()
        return time.time() + 86_400


class QuotaGuardian:
    """
    Watches every provider's free-tier quota.
    """

    WARNING_THRESHOLD = 0.80
    ROTATION_THRESHOLD = 0.95

    def __init__(self, router=None) -> None:
        self._quotas: dict[str, ProviderQuota] = {}
        self._router = router
        self._running = False
        self._task: asyncio.Task | None = None

    def register_provider(self, provider_name: str) -> None:
        limits = FREE_TIER_LIMITS.get(provider_name, {})
        quota = ProviderQuota(
            provider=provider_name,
            tokens_limit=limits.get("tokens_per_day", limits.get("tokens_per_month", 0)),
            requests_limit=limits.get("requests_per_day", limits.get("requests_per_month", 0)),
            reset_policy=limits.get("resets", "daily_utc_midnight"),
        )
        quota.next_reset = quota._next_reset_time()
        self._quotas[provider_name] = quota

    def record_usage(self, provider: str, tokens: int, requests: int = 1) -> None:
        quota = self._quotas.get(provider)
        if quota is None:
            return

        quota.tokens_used += tokens
        quota.requests_used += requests
        quota.last_checked = time.time()

        util = quota.utilisation
        if util >= self.ROTATION_THRESHOLD and not quota.exhausted:
            quota.exhausted = True
            logger.warning(
                f"Provider '{provider}' quota at {util * 100:.0f}% - rotating to next best model"
            )
            if self._router:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and not loop.is_closed():
                    loop.create_task(self._router.mark_provider_quota(provider, quota.next_reset))
        elif util >= self.WARNING_THRESHOLD:
            logger.info(
                f"Provider '{provider}' at {util * 100:.0f}% - {quota.tokens_remaining:,} tokens remaining"
            )

    def get_health(self) -> dict[str, dict]:
        return {
            name: {
                "utilisation": q.utilisation,
                "utilisation_label": f"{q.utilisation * 100:.1f}%",
                "tokens_used": q.tokens_used,
                "tokens_limit": q.tokens_limit or "unlimited",
                "tokens_remaining": q.tokens_remaining if q.tokens_limit else "unlimited",
                "requests_used": q.requests_used,
                "requests_remaining": q.requests_remaining,
                "exhausted": q.exhausted,
                "resets": q.reset_policy,
            }
            for name, q in self._quotas.items()
        }

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Quota Guardian started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                for quota in self._quotas.values():
                    if quota.should_reset():
                        quota.do_reset()
                        if self._router:
                            await self._router.reset_provider_quotas(quota.provider)
            except Exception as exc:
                logger.error(f"Quota guardian error: {exc}")
            await asyncio.sleep(60)
