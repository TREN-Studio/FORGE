from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_CACHE_DIR = Path.home() / ".forge" / "skill_cache"
_CACHE_TTL = 300  # 5 minutes


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"trending_{key}.json"


def _read_cache(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < _CACHE_TTL:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _write_cache(key: str, data: dict) -> None:
    try:
        data["_ts"] = time.time()
        _cache_path(key).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _fetch_google_trends(niche: str, limit: int = 10) -> list[dict]:
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=360, timeout=10)
        pytrends.build_payload([niche], cat=0, timeframe="now 7-d", geo="", gprop="")
        related = pytrends.related_queries()
        results: list[dict] = []

        if niche in related and related[niche] is not None:
            top = related[niche].get("top")
            if top is not None and not top.empty:
                for _, row in top.head(limit).iterrows():
                    results.append({
                        "query": str(row.get("query", "")),
                        "value": int(row.get("value", 0)),
                        "source": "google_trends_top",
                    })

            rising = related[niche].get("rising")
            if rising is not None and not rising.empty:
                for _, row in rising.head(limit).iterrows():
                    results.append({
                        "query": str(row.get("query", "")),
                        "value": int(row.get("value", 0)),
                        "source": "google_trends_rising",
                    })

        trending = pytrends.trending_searches()
        if trending is not None and not trending.empty:
            for _, row in trending.head(limit).iterrows():
                results.append({
                    "query": str(row[0]),
                    "value": 100,
                    "source": "google_trends_daily",
                })

        return results[:limit]
    except ImportError:
        raise RuntimeError("pytrends is not installed. Run: pip install pytrends")
    except Exception as exc:
        raise RuntimeError(f"Google Trends API error: {exc}")


def _fetch_amazon_bsr(niche: str, limit: int = 10) -> list[dict]:
    import requests
    from bs4 import BeautifulSoup

    niche_slugs = {
        "tech": "electronics",
        "fitness": "sports-outdoors",
        "kitchen": "kitchen",
        "home": "home-garden",
        "books": "books",
        "tools": "tools-home-improvement",
        "toys": "toys-games",
        "beauty": "beauty",
        "office": "office-products",
        "pet": "pet-supplies",
        "automotive": "automotive",
        "music": "musical-instruments",
        "video": "movies-tv",
        "clothing": "fashion",
        "shoes": "fashion",
        "baby": "baby-products",
        "grocery": "grocery-gourmet-food",
        "health": "health-personal-care",
        "sports": "sports-outdoors",
        "garden": "home-garden",
    }

    slug = niche_slugs.get(niche.lower().strip(), niche.lower().strip())
    url = f"https://www.amazon.com/gp/bestsellers/{slug}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Amazon BSR fetch error: {exc}")

    soup = BeautifulSoup(resp.text, "lxml")
    items = soup.select("[data-component-type='s-impression-counter']")
    if not items:
        items = soup.select(".p13n-sc-truncate, .a-carousel-card, .zg-grid-general-faceout")

    results: list[dict] = []
    seen = set()

    for item in items:
        if len(results) >= limit:
            break

        title_el = item.select_one("._cDEzb_p13n-sc-css-line-clamp-1_g3R1c, .p13n-sc-truncate, img[alt]")
        link_el = item.select_one("a[href*='/dp/']")
        price_el = item.select_one("._cDEzb_p13n-sc-price_3mJ9Z, .p13n-sc-price, span.a-price-whole")
        bsr_el = item.select_one(".zg-badge-text, .a-size-small.a-link-child")
        img_el = item.select_one("img[alt]")

        title = ""
        if title_el:
            if title_el.name == "img":
                title = title_el.get("alt", "")
            else:
                title = title_el.get_text(strip=True)
        if not title and img_el:
            title = img_el.get("alt", "")

        if not title or title in seen:
            continue
        seen.add(title)

        link = ""
        if link_el:
            href = link_el.get("href", "")
            if "/dp/" in href:
                link = "https://www.amazon.com" + href.split("/ref")[0]

        price = ""
        if price_el:
            price = price_el.get_text(strip=True)

        bsr = ""
        if bsr_el:
            bsr = bsr_el.get_text(strip=True)

        img = ""
        if img_el:
            img = img_el.get("src", "")

        results.append({
            "title": title,
            "url": link,
            "price": price,
            "bsr": bsr,
            "image": img,
            "source": "amazon_bsr",
        })

    return results


def _build_insights(niche: str, trends: list[dict], bsr: list[dict]) -> str:
    lines = [f"# Trending Research: {niche}", f"_Generated: {datetime.now().isoformat()}_", ""]

    if trends:
        lines.append("## Google Trends")
        lines.append("| Query | Score | Source |")
        lines.append("|-------|-------|--------|")
        for t in trends[:10]:
            src = t.get("source", "").replace("google_trends_", "")
            lines.append(f"| {t.get('query', '')} | {t.get('value', 0)} | {src} |")
        lines.append("")

    if bsr:
        lines.append("## Amazon Best Sellers")
        lines.append("| Product | Price | BSR |")
        lines.append("|---------|-------|-----|")
        for p in bsr[:10]:
            lines.append(f"| {p.get('title', '')} | {p.get('price', 'N/A')} | {p.get('bsr', 'N/A')} |")
        lines.append("")

    if not trends and not bsr:
        lines.append("_No trend data available for this niche._")

    return "\n".join(lines)


def execute(payload: dict, context) -> dict:
    sanitizer = getattr(context, "sanitizer", None)
    safe_request = (
        sanitizer.sanitize_text(payload["request"], source="user_request")
        if sanitizer else payload["request"]
    )

    niche = payload.get("niche") or payload.get("objective", safe_request)
    limit = min(payload.get("limit", 10), 50)

    trends: list[dict] = []
    bsr: list[dict] = []
    errors: list[str] = []
    status = "completed"

    # Google Trends
    cache_key_t = f"gt_{niche.lower().replace(' ', '_')}"
    cached = _read_cache(cache_key_t)
    if cached:
        trends = cached.get("data", [])
    else:
        try:
            trends = _fetch_google_trends(niche, limit)
            _write_cache(cache_key_t, {"data": trends})
        except RuntimeError as e:
            errors.append(f"Google Trends: {e}")

    # Amazon BSR
    cache_key_a = f"bsr_{niche.lower().replace(' ', '_')}"
    cached = _read_cache(cache_key_a)
    if cached:
        bsr = cached.get("data", [])
    else:
        try:
            bsr = _fetch_amazon_bsr(niche, limit)
            _write_cache(cache_key_a, {"data": bsr})
        except RuntimeError as e:
            errors.append(f"Amazon BSR: {e}")

    if errors and not trends and not bsr:
        status = "failed"
    elif errors and (trends or bsr):
        status = "partial"

    insights = _build_insights(niche, trends, bsr)

    result: dict[str, Any] = {
        "status": status,
        "google_trends": trends,
        "amazon_bsr": bsr,
        "combined_insights": insights,
    }
    if errors:
        result["error"] = "; ".join(errors)

    return result
