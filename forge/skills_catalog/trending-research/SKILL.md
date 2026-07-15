---
name: trending-research
description: Fetches trending topics from Google Trends and Amazon Best Sellers Rank data
category: research
version: 1.0.0
---

# Purpose
Fetch real-time trending keywords from Google Trends and Amazon Best Sellers data to inform content strategy and affiliate product selection.

# When to use
- When the user wants trending topics, viral keywords, or what's popular right now.
- When an SEO or content skill needs real search trend data before writing.
- When the user asks about Amazon BSR, best sellers, or hot products in a niche.

# When not to use
- When the user only needs analysis of existing content.
- When the task is purely file manipulation or code execution.
- When network access is unavailable (Google Trends API requires internet).

# Inputs
- request: the raw user request
- objective: the execution objective
- niche: optional niche or category to scope the research (e.g. "tech", "fitness", "kitchen")
- limit: max number of results per source (default: 10)

# Outputs
- status
- google_trends: list of trending keywords with scores
- amazon_bsr: list of top products with BSR, title, price, and category
- combined_insights: markdown summary of findings

# Execution Rules
- Google Trends: fetch related queries for the niche, return top rising terms.
- Amazon BSR: scrape or call Amazon Best Sellers page for the given niche.
- Cache results to avoid hitting rate limits (5 min TTL).
- Never fabricate trend data. If the API fails, return partial results with an error flag.

# Validation
- google_trends must contain at least one entry when trends API succeeds.
- amazon_bsr must not be empty when Amazon scraper returns results.
- combined_insights must be non-empty markdown.

# Safety
- Do not abuse Google Trends rate limits (max 1 request per 60s per keyword).
- Amazon scraping respects robots.txt; use polite delays.
- Never expose API keys or tokens in skill output.

# Failure Modes
- Google Trends API rate-limited or blocked.
- Amazon BSR page structure changed.
- Network unreachable.
- Niche too niche for trend data.

# Fallback
- Return available data from whichever source succeeded.
- If both fail, return a clear error and suggest the user try again later.

# Response Style
Structured markdown with tables for trend data and product listings.
