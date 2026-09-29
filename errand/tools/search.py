"""Web search.

Tier 0, but the results are third-party text, so snippets go through the same
cleaning path as an email subject. Fetching and reading a full page is a
reader job, not a planner one.
"""

from __future__ import annotations

from typing import Any

from errand.policy.tiers import Tier
from errand.reader import quarantine
from errand.reader.schemas import clean_text
from errand.tools import providers
from errand.tools.registry import tool


@tool(
    "web_search",
    tier=Tier.READ,
    summary="Search the web",
    domain="web",
    describe=lambda a: f"search the web for {a.get('query', '')!r}",
)
def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    results = providers.get_providers().search.search(query, max(1, min(int(limit), 10)))
    return {
        "query": query,
        "results": [
            {
                "title": clean_text(r.get("title", ""), 160),
                "url": clean_text(r.get("url", ""), 300),
                "snippet": clean_text(r.get("snippet", ""), 300),
            }
            for r in results
        ],
    }


@tool(
    "web_read",
    tier=Tier.READ,
    summary="Read a web page through the quarantined reader",
    domain="web",
    describe=lambda a: f"read {a.get('url', '')}",
)
def web_read(url: str, html: str = "") -> dict[str, Any]:
    """The page text is handed to the reader, never to the planner.

    `html` is passed in by whatever fetched the page (v1 gives this the
    AgentCore Browser). Keeping the fetch outside this function means the
    planner cannot aim a fetch at an internal address.
    """
    result = quarantine.read_web(html, source=url)
    return {
        "url": url,
        "extract": result.data,
        "dropped_keys": result.dropped_keys,
        "flagged_as_instructions": result.flagged,
    }
