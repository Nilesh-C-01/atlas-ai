"""
ai/tools.py

Tool schemas (Claude tool-calling format) + implementations.
Each tool: (1) schema dict for Claude, (2) async function that executes it.
Every external call is wrapped so a failure returns a message Claude can talk
around, instead of raising and killing the conversation.
"""

from __future__ import annotations

import httpx
from typing import Any

from app.config import settings

FINNHUB_API_KEY = settings.finnhub_api_key
FINNHUB_BASE = "https://finnhub.io/api/v1"


# ---------------------------------------------------------------------------
# Claude tool schemas — passed as `tools=[...]` in the Messages API call
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_quote",
        "description": (
            "Get the current stock price, day change, and % change for a ticker. "
            "Use this whenever the user asks about a stock's current price or "
            "how it's doing today."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol, e.g. AAPL, MSFT, TSLA",
                }
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_company_profile",
        "description": (
            "Get company overview: name, industry, market cap, description, "
            "exchange, IPO date. Use for 'tell me about [company]' type requests "
            "when the user wants a general overview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_news",
        "description": (
            "Get recent news headlines for a specific company ticker, with "
            "summaries and source. Use when user asks 'what's the latest news "
            "on X' or 'why did X move today'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"},
                "days_back": {
                    "type": "integer",
                    "description": "How many days of news to look back. Default 3.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_earnings",
        "description": (
            "Get upcoming or recent earnings data for a ticker: report date, "
            "EPS estimate vs actual, revenue estimate vs actual. Use for "
            "earnings-related questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "compare_companies",
        "description": (
            "Compare 2+ companies side by side on price, market cap, and basic "
            "fundamentals. Use when user asks to compare tickers, e.g. "
            "'Microsoft vs Google'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 2-4 ticker symbols to compare",
                }
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "read_sheet",
        "description": (
            "Read the contents of a Google Sheet from a shared link and return "
            "its data so it can be analyzed. Use when the user shares a Google "
            "Sheets URL and asks a question about it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_url": {
                    "type": "string",
                    "description": "The Google Sheets share URL provided by the user",
                }
            },
            "required": ["sheet_url"],
        },
    },
    {
        "name": "save_memory_fact",
        "description": (
            "Save a durable fact about the user for future conversations — "
            "e.g. their role, companies/sectors they follow, preferences, "
            "briefing schedule. Call this whenever the user shares something "
            "worth remembering long-term. Do not call for trivial or one-off "
            "details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "A short, self-contained fact, e.g. "
                        "'Follows semiconductor and AI stocks' or "
                        "'Wants daily brief at 8am IST'"
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": ["role", "watchlist", "preference", "schedule", "other"],
                },
            },
            "required": ["fact", "category"],
        },
    },
]


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

async def _finnhub_get(path: str, params: dict[str, Any]) -> dict | list | None:
    params = {**params, "token": FINNHUB_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{FINNHUB_BASE}/{path}", params=params)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, httpx.TimeoutException):
        return None


async def get_quote(ticker: str) -> dict[str, Any]:
    data = await _finnhub_get("quote", {"symbol": ticker.upper()})
    if not data or data.get("c") is None:
        return {"error": f"Couldn't fetch a live quote for {ticker.upper()} right now."}
    return {
        "ticker": ticker.upper(),
        "current_price": data["c"],
        "change": data["d"],
        "percent_change": data["dp"],
        "high": data["h"],
        "low": data["l"],
        "prev_close": data["pc"],
    }


async def get_company_profile(ticker: str) -> dict[str, Any]:
    data = await _finnhub_get("stock/profile2", {"symbol": ticker.upper()})
    if not data or not data.get("name"):
        return {"error": f"Couldn't find profile data for {ticker.upper()}."}
    return {
        "ticker": ticker.upper(),
        "name": data.get("name"),
        "industry": data.get("finnhubIndustry"),
        "market_cap_musd": data.get("marketCapitalization"),
        "exchange": data.get("exchange"),
        "ipo_date": data.get("ipo"),
        "website": data.get("weburl"),
    }


async def get_news(ticker: str, days_back: int = 3) -> dict[str, Any]:
    import datetime

    to_date = datetime.date.today()
    from_date = to_date - datetime.timedelta(days=days_back)
    data = await _finnhub_get(
        "company-news",
        {
            "symbol": ticker.upper(),
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
    )
    if not data:
        return {"error": f"Couldn't fetch news for {ticker.upper()} right now."}
    top = data[:5]
    return {
        "ticker": ticker.upper(),
        "headlines": [
            {
                "headline": item.get("headline"),
                "summary": item.get("summary"),
                "source": item.get("source"),
                "url": item.get("url"),
                "datetime": item.get("datetime"),
            }
            for item in top
        ],
    }


async def get_earnings(ticker: str) -> dict[str, Any]:
    data = await _finnhub_get(
        "stock/earnings", {"symbol": ticker.upper()}
    )
    if not data:
        return {"error": f"Couldn't fetch earnings data for {ticker.upper()}."}
    return {"ticker": ticker.upper(), "recent_earnings": data[:4]}


async def compare_companies(tickers: list[str]) -> dict[str, Any]:
    results = {}
    for t in tickers[:4]:
        quote = await get_quote(t)
        profile = await get_company_profile(t)
        results[t.upper()] = {**quote, **profile}
    return {"comparison": results}


async def save_memory_fact(fact: str, category: str, user_id: int, db) -> dict[str, Any]:
    # db is an injected connection/session; kept generic here since ORM choice
    # is finalized in db/queries.py. This is the call signature the agent
    # loop should bind user_id/db into before exposing this to Claude.
    await db.execute(
        "INSERT INTO memory_facts (user_id, fact, category) VALUES ($1, $2, $3)",
        user_id, fact, category,
    )
    return {"saved": True}


# read_sheet lives in integrations/sheets.py and gets imported into the
# dispatch table in agent.py — kept out of this file to avoid mixing
# Google auth setup into the tools module.

TOOL_DISPATCH = {
    "get_quote": get_quote,
    "get_company_profile": get_company_profile,
    "get_news": get_news,
    "get_earnings": get_earnings,
    "compare_companies": compare_companies,
    # "read_sheet": read_sheet,           -> bound in agent.py
    # "save_memory_fact": save_memory_fact -> bound in agent.py (needs user_id/db)
}
