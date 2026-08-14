"""
ai/tools.py

Tool schemas (Claude tool-calling format) + implementations.
Each tool: (1) schema dict for Claude, (2) async function that executes it.
Every external call is wrapped so a failure returns a message Claude can talk
around, instead of raising and killing the conversation.
"""

from __future__ import annotations

import asyncio
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
        "name": "get_market_news",
        "description": (
            "Get broad, market-wide news headlines — not tied to one ticker. "
            "Use this for general/macro/breaking financial news, economic "
            "events, or regulatory announcements — e.g. 'what's happening in "
            "the market today', a morning/evening market brief, or anything "
            "not specific to a single company (use get_news for that instead)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "News category: general (default), forex, crypto, or merger",
                }
            },
            "required": [],
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
    {
        "name": "set_briefing_time",
        "description": (
            "Set what time the user wants to receive their proactive daily "
            "brief. Call this whenever the user tells you a preferred time "
            "for their daily update — e.g. 'send my brief at 8am' or "
            "'I want updates every morning around 9'. Give the time in the "
            "user's OWN local time exactly as they said it — do NOT convert "
            "to UTC yourself, the backend stores it as local time and "
            "resolves it correctly (including DST) at send-time using their "
            "timezone. You can call this immediately regardless of whether "
            "you know their timezone yet — it does not depend on set_timezone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "local_time_24h": {
                    "type": "string",
                    "description": (
                        "The requested time in 24-hour HH:MM format, in the "
                        "user's OWN local time — just convert 12h to 24h "
                        "notation (e.g. '8am' -> '08:00'), no timezone math."
                    ),
                },
            },
            "required": ["local_time_24h"],
        },
    },
    {
        "name": "set_timezone",
        "description": (
            "Store the user's timezone — used to resolve their briefing "
            "time correctly and to tell them the accurate current local "
            "time. Call this as soon as the user tells you their timezone "
            "or city — never call it with a placeholder or guessed value, "
            "only once you actually know it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Best guess at the IANA zone name (e.g. 'Asia/Kolkata') from what the user said (a city or 'IST' etc is fine too, common labels are recognized)",
                },
            },
            "required": ["timezone"],
        },
    },
    {
        "name": "add_to_watchlist",
        "description": (
            "Add a ticker to the user's tracked watchlist. Call this whenever "
            "the user asks you to track, watch, monitor, or keep an eye on a "
            "stock/company — e.g. 'track Tesla for me' or 'keep an eye on "
            "NVDA'. This powers their proactive daily brief and price alerts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol, e.g. 'TSLA'",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "set_price_alert",
        "description": (
            "Set a target-price alert for a ticker — the user gets notified "
            "once when the price crosses this level. Call this when the user "
            "asks to be alerted at a specific price, e.g. 'alert me if AAPL "
            "hits $200' or 'let me know if TSLA drops below 300'. This is in "
            "addition to the automatic >5% daily-move alert every tracked "
            "ticker already gets — only call this for an explicit price "
            "target."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol, e.g. 'TSLA'",
                },
                "target_price": {
                    "type": "number",
                    "description": "The price level to alert at",
                },
            },
            "required": ["ticker", "target_price"],
        },
    },
    {
        "name": "continue_onboarding",
        "description": (
            "Call this exactly once per turn while onboarding is in progress "
            "(the system prompt will tell you if it is). If the user just "
            "answered the current onboarding question, set stop=false to "
            "move to the next one. If the user skipped, declined, said "
            "something like 'later'/'skip', or asked about something "
            "unrelated instead of answering, set stop=true — onboarding "
            "ends for good and you just help with whatever they actually "
            "asked. Never call this outside of onboarding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stop": {
                    "type": "boolean",
                    "description": "true to end onboarding now, false to advance to the next question",
                },
            },
            "required": ["stop"],
        },
    },
    {
        "name": "get_google_connect_link",
        "description": (
            "Get a link the user can click to connect their Google account "
            "(Gmail + Calendar, one combined consent). Call this when the "
            "user wants to connect Gmail/Calendar, or when they ask you to "
            "do something needing Gmail/Calendar access and they haven't "
            "connected yet (a gmail/calendar tool call will tell you this "
            "via a 'not connected' error — call this in response). Just "
            "send them the link as plain text; it's a real clickable URL, "
            "not a Telegram button."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "note_google_offer_declined",
        "description": (
            "Call this when the user explicitly declines or skips a "
            "proactive offer to connect their Google account (Gmail/ "
            "Calendar/Drive) — e.g. they say 'no thanks', 'skip', 'maybe "
            "later', 'not now'. Do NOT call this when they haven't been "
            "offered anything, or when a Google feature failed for an "
            "unrelated reason. This tracks how many times they've said no "
            "so you know to stop bringing it up unprompted after a couple "
            "of skips."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_gmail",
        "description": (
            "Search the user's Gmail for messages matching a query. Uses "
            "standard Gmail search syntax (e.g. 'from:boss@company.com', "
            "'subject:earnings', 'newer_than:7d Tesla'). Call this when the "
            "user asks about emails, e.g. 'search my emails about this "
            "company' or 'any emails from my broker recently'. Requires the "
            "user to have connected their Google account first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'Tesla newer_than:7d'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": (
            "List the user's upcoming Google Calendar events. Call this "
            "when they ask what's on their calendar, what meetings they "
            "have coming up, etc. Requires a connected Google account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Max number of upcoming events to return (default 10)",
                },
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create a Google Calendar event — for scheduling a meeting or "
            "setting a reminder (e.g. 'remind me an hour before Apple's "
            "earnings call', 'schedule a meeting with my team tomorrow at "
            "3pm'). Requires a connected Google account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title, e.g. 'Apple earnings call reminder'",
                },
                "start_iso": {
                    "type": "string",
                    "description": "Start time in ISO 8601 with timezone offset, e.g. '2026-08-15T14:00:00+05:30'",
                },
                "end_iso": {
                    "type": "string",
                    "description": "End time in ISO 8601 with timezone offset. For a reminder with no natural duration, use start_iso + 15 minutes.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description/notes",
                },
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
    {
        "name": "search_drive_files",
        "description": (
            "Search the user's Google Drive by file name — e.g. 'find that "
            "earnings deck', 'search my drive for the due diligence doc'. "
            "Returns file names, types, and IDs; use read_drive_file with "
            "the id to actually read one. Requires a connected Google account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text to match against file names",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_drive_file",
        "description": (
            "Read a Google Drive item by id, from a prior search_drive_files "
            "call. If it's a Google Doc, Sheet, PDF, or plain text file, "
            "returns its text content so you can answer questions about it. "
            "If it's a FOLDER, returns the list of files/folders inside it "
            "instead (use this to browse into a folder — e.g. if the user "
            "mentions a folder name and search_drive_files only matched the "
            "folder itself, call read_drive_file on that folder's id to see "
            "what's inside, then read_drive_file again on the file you find). "
            "If it's an image, returns its name and link (no text to "
            "extract). Requires a connected Google account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The Drive file id, from a prior search_drive_files call",
                },
            },
            "required": ["file_id"],
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


async def get_market_news(category: str = "general") -> dict[str, Any]:
    data = await _finnhub_get("news", {"category": category})
    if not data:
        return {"error": "Couldn't fetch market news right now."}
    top = data[:8]
    return {
        "category": category,
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
    async def _one(t: str) -> tuple[str, dict[str, Any]]:
        quote = await get_quote(t)
        profile = await get_company_profile(t)
        return t.upper(), {**quote, **profile}

    pairs = await asyncio.gather(*(_one(t) for t in tickers[:4]))
    return {"comparison": dict(pairs)}


# save_memory_fact is handled entirely in agent.py's _dispatch_tool (it needs
# a live user_id/db that only agent.py has) — the actual DB write is
# add_memory_fact in db/queries.py. There's deliberately no implementation
# here to avoid two divergent code paths for the same tool.

# read_sheet lives in integrations/sheets.py and gets imported into the
# dispatch table in agent.py — kept out of this file to avoid mixing
# Google auth setup into the tools module.

TOOL_DISPATCH = {
    "get_quote": get_quote,
    "get_company_profile": get_company_profile,
    "get_news": get_news,
    "get_market_news": get_market_news,
    "get_earnings": get_earnings,
    "compare_companies": compare_companies,
    # "read_sheet": read_sheet,           -> bound in agent.py
    # "save_memory_fact"                  -> bound in agent.py (needs user_id/db)
}
