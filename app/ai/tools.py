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
FRED_API_KEY = settings.fred_api_key


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
            "Get recent + next earnings data for a ticker: report date, "
            "EPS estimate vs actual, revenue estimate vs actual, and — for "
            "the next upcoming report — the market session it's expected in "
            "('bmo' = before market open, 'amc' = after market close, 'dmh' "
            "= during market hours). There is NO earnings call transcript "
            "or audio/text summary of what was actually SAID on the call — "
            "if asked to 'summarize the call', be upfront that you only "
            "have the numbers, not the call content itself, then summarize "
            "what you do have (the figures, beat/miss, and any related "
            "news via get_news) rather than inventing call commentary. The "
            "'hour' field is a session label, not an exact clock time — if "
            "asked to time something relative to it (e.g. a reminder), say "
            "so plainly and treat it as an estimate."
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
            "Sheets URL and asks a question about it. The result includes BOTH "
            "raw CSV text (for qualitative reading, may be truncated on a huge "
            "sheet) AND computed_stats — exact count/sum/mean/min/max plus "
            "outliers (values outside the normal IQR range) per numeric "
            "column, computed from the FULL sheet regardless of truncation. "
            "For any sum/average/total/anomaly/outlier question, use "
            "computed_stats — never count or add up numbers yourself from the "
            "raw text, that's unreliable and exactly what computed_stats exists "
            "to avoid."
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
        "name": "set_custom_move_alert",
        "description": (
            "Override the default 5% daily-move alert threshold for one "
            "ticker on the watchlist. Every tracked ticker already alerts "
            "automatically at a 5% daily move — only call this when the "
            "user explicitly asks for a DIFFERENT percentage, e.g. 'alert "
            "me if TSLA moves more than 3% in a day'. Also adds the ticker "
            "to the watchlist if it isn't already there."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol, e.g. 'TSLA'",
                },
                "percent": {
                    "type": "number",
                    "description": "The daily move threshold in percent, e.g. 3 for 3%",
                },
            },
            "required": ["ticker", "percent"],
        },
    },
    {
        "name": "set_reminder",
        "description": (
            "Schedule a one-off reminder message for a specific future local "
            "date/time, e.g. 'remind me an hour before Apple's earnings "
            "call' or 'remind me tomorrow at 3pm to check on NVDA'. Requires "
            "knowing the user's timezone first (from stored prefs or ask "
            "them) — never guess it. Figure out the exact target date/time "
            "yourself from the current date/time given to you, the user's "
            "request, and (if relevant) real data from another tool call "
            "like get_earnings — do the simple arithmetic yourself (e.g. "
            "'earnings is amc on 2026-01-28, so remind 1 hour before my "
            "best estimate of that session's end, and say it's an "
            "estimate'), don't ask the user to do the math. This is for a "
            "single future moment, not a recurring daily thing — for a "
            "recurring daily briefing use set_briefing_time instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "local_datetime": {
                    "type": "string",
                    "description": "Target date/time in the user's own local time, format 'YYYY-MM-DD HH:MM' (24h)",
                },
                "message": {
                    "type": "string",
                    "description": "What to remind them about — short, plain text",
                },
            },
            "required": ["local_datetime", "message"],
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
            "Search the user's Google Drive by file name AND by content — "
            "e.g. 'find that earnings deck', 'search my drive for the due "
            "diligence doc', or 'find the file that mentions Q3 revenue' "
            "(content search covers Docs, Sheets, Slides, PDFs with a text "
            "layer, and plain text files). Returns file names, types, and "
            "IDs; use read_drive_file with the id to actually read one. "
            "Requires a connected Google account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text to match against file names or file content",
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
    {
        "name": "get_sec_filings",
        "description": (
            "Get a company's real recent SEC filings directly from SEC "
            "EDGAR (form type, filing date, and a direct link to the actual "
            "filing) — this IS the real regulatory filing record, not a "
            "news-based proxy. Use for 'SEC filings', '10-K', '10-Q', "
            "'8-K', or 'regulatory filings' questions. US-listed companies "
            "only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. 'AAPL'"},
                "form_type": {
                    "type": "string",
                    "description": "Optional filing type filter, e.g. '10-K', '10-Q', '8-K'. Omit for all recent filings.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_insider_transactions",
        "description": (
            "Get recent insider buy/sell transactions for a ticker (who, "
            "how many shares, transaction type, date, price). Use for "
            "questions about insider trading activity or executive/board "
            "transactions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. 'AAPL'"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_financial_ratios",
        "description": (
            "Get key financial ratios and metrics for a ticker — P/E, P/B, "
            "margins, ROE, current ratio, debt-to-equity, 52-week range, "
            "and similar. Use for valuation, profitability, or financial-health "
            "questions that need real ratios rather than raw price/earnings alone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. 'AAPL'"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_analyst_ratings",
        "description": (
            "Get recent analyst recommendation trends for a ticker — counts "
            "of strong buy/buy/hold/sell/strong sell ratings by month. Use "
            "for 'analyst activity'/'what do analysts think'/'is this a buy' "
            "questions — real aggregated ratings, not your own opinion. Note: "
            "individual analyst price targets aren't available, only the "
            "rating-count breakdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. 'AAPL'"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_economic_indicator",
        "description": (
            "Get a real macroeconomic time series from FRED (Federal "
            "Reserve Economic Data) — e.g. inflation (CPI), unemployment "
            "rate, Fed funds rate, GDP. Use for questions about economic "
            "conditions/indicators rather than guessing or relying on news "
            "headlines alone. If this returns a 'not configured' error, "
            "tell the user plainly that this data source isn't set up yet "
            "rather than answering from general knowledge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "series_id": {
                    "type": "string",
                    "description": (
                        "FRED series ID. Common ones: CPIAUCSL (inflation/CPI), "
                        "UNRATE (unemployment rate), FEDFUNDS (Fed funds rate), "
                        "GDP (gross domestic product), DGS10 (10-year Treasury yield)."
                    ),
                },
            },
            "required": ["series_id"],
        },
    },
    {
        "name": "get_sec_full_text_search",
        "description": (
            "Search the full TEXT of SEC filings (not just filing "
            "metadata) via EDGAR's full-text search — real filings only, "
            "since ~2001. Each result includes an 'items' field listing "
            "the SEC item codes triggered (e.g. '5.02' = officer/director "
            "departure or election = LEADERSHIP CHANGE, '1.01' = entry "
            "into a material agreement, '2.01' = completion of "
            "acquisition/disposition = M&A). Use this for leadership "
            "changes, M&A, and funding/material-agreement questions — "
            "there's no dedicated 'leadership changes' or 'M&A' data feed, "
            "this real filing search is the grounded way to answer those. "
            "If nothing relevant turns up, say so — don't guess."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search phrase, e.g. 'chief executive officer resignation', 'merger agreement'",
                },
                "ticker": {
                    "type": "string",
                    "description": "Optional — scope the search to one company's filings",
                },
                "form_types": {
                    "type": "string",
                    "description": "Optional comma-separated form types to filter, e.g. '8-K' or '8-K,10-K'",
                },
            },
            "required": ["query"],
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
    import datetime

    data = await _finnhub_get("stock/earnings", {"symbol": ticker.upper()})
    if not data:
        return {"error": f"Couldn't fetch earnings data for {ticker.upper()}."}

    result: dict[str, Any] = {"ticker": ticker.upper(), "recent_earnings": data[:4]}

    # Look ahead ~120 days for the next scheduled report — the free
    # calendar/earnings endpoint only gives a date + session label (bmo/amc/
    # dmh), never an exact clock time.
    today = datetime.date.today()
    calendar = await _finnhub_get(
        "calendar/earnings",
        {
            "from": today.isoformat(),
            "to": (today + datetime.timedelta(days=120)).isoformat(),
            "symbol": ticker.upper(),
        },
    )
    upcoming = (calendar or {}).get("earningsCalendar") or []
    if upcoming:
        next_report = upcoming[0]
        result["next_earnings"] = {
            "date": next_report.get("date"),
            "session": next_report.get("hour"),  # "bmo" | "amc" | "dmh" — not an exact time
            "eps_estimate": next_report.get("epsEstimate"),
            "revenue_estimate": next_report.get("revenueEstimate"),
        }
    return result


async def compare_companies(tickers: list[str]) -> dict[str, Any]:
    async def _one(t: str) -> tuple[str, dict[str, Any]]:
        quote = await get_quote(t)
        profile = await get_company_profile(t)
        return t.upper(), {**quote, **profile}

    pairs = await asyncio.gather(*(_one(t) for t in tickers[:4]))
    return {"comparison": dict(pairs)}


async def get_insider_transactions(ticker: str) -> dict[str, Any]:
    data = await _finnhub_get("stock/insider-transactions", {"symbol": ticker.upper()})
    transactions = (data or {}).get("data")
    if not transactions:
        return {"error": f"Couldn't fetch insider transactions for {ticker.upper()} right now."}
    top = transactions[:10]
    return {
        "ticker": ticker.upper(),
        "transactions": [
            {
                "name": t.get("name"),
                "shares": t.get("share"),
                "change": t.get("change"),
                "transaction_date": t.get("transactionDate"),
                "transaction_code": t.get("transactionCode"),
                "price": t.get("transactionPrice"),
            }
            for t in top
        ],
    }


async def get_financial_ratios(ticker: str) -> dict[str, Any]:
    data = await _finnhub_get("stock/metric", {"symbol": ticker.upper(), "metric": "all"})
    metrics = (data or {}).get("metric")
    if not metrics:
        return {"error": f"Couldn't fetch financial ratios for {ticker.upper()} right now."}
    return {
        "ticker": ticker.upper(),
        "pe_ratio_ttm": metrics.get("peBasicExclExtraTTM"),
        "pb_ratio": metrics.get("pbAnnual"),
        "roe_ttm": metrics.get("roeTTM"),
        "roa_ttm": metrics.get("roaTTM"),
        "gross_margin_ttm": metrics.get("grossMarginTTM"),
        "net_margin_ttm": metrics.get("netProfitMarginTTM"),
        "current_ratio": metrics.get("currentRatioAnnual"),
        "debt_to_equity": metrics.get("totalDebt/totalEquityAnnual"),
        "52_week_high": metrics.get("52WeekHigh"),
        "52_week_low": metrics.get("52WeekLow"),
        "revenue_growth_ttm_yoy": metrics.get("revenueGrowthTTMYoy"),
    }


async def get_analyst_ratings(ticker: str) -> dict[str, Any]:
    data = await _finnhub_get("stock/recommendation", {"symbol": ticker.upper()})
    if not data:
        return {"error": f"Couldn't fetch analyst ratings for {ticker.upper()} right now."}
    top = data[:6]
    return {
        "ticker": ticker.upper(),
        "monthly_ratings": [
            {
                "period": r.get("period"),
                "strong_buy": r.get("strongBuy"),
                "buy": r.get("buy"),
                "hold": r.get("hold"),
                "sell": r.get("sell"),
                "strong_sell": r.get("strongSell"),
            }
            for r in top
        ],
    }


_SEC_HEADERS = {"User-Agent": "Atlas AI Financial Assistant nilesh.choudhury01@gmail.com"}
_sec_ticker_to_cik: dict[str, str] | None = None


async def _load_sec_ticker_map(client: httpx.AsyncClient) -> dict[str, str]:
    global _sec_ticker_to_cik
    if _sec_ticker_to_cik is not None:
        return _sec_ticker_to_cik
    resp = await client.get("https://www.sec.gov/files/company_tickers.json", headers=_SEC_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    _sec_ticker_to_cik = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}
    return _sec_ticker_to_cik


async def get_sec_filings(ticker: str, form_type: str | None = None) -> dict[str, Any]:
    ticker = ticker.upper()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            ticker_map = await _load_sec_ticker_map(client)
            cik = ticker_map.get(ticker)
            if cik is None:
                return {"error": f"No SEC EDGAR record found for {ticker} — may not be a US-listed company."}

            resp = await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_SEC_HEADERS)
            resp.raise_for_status()
            recent = resp.json().get("filings", {}).get("recent", {})
    except (httpx.HTTPError, httpx.TimeoutException):
        return {"error": f"Couldn't reach SEC EDGAR for {ticker} right now."}

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    filings = []
    for form, date, accession, doc in zip(forms, dates, accessions, docs):
        if form_type and form.upper() != form_type.upper():
            continue
        accession_no_dashes = accession.replace("-", "")
        filings.append(
            {
                "form": form,
                "filing_date": date,
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{doc}",
            }
        )
        if len(filings) >= 10:
            break

    if not filings:
        return {"ticker": ticker, "filings": [], "note": "No matching filings found."}
    return {"ticker": ticker, "filings": filings}


# SEC 8-K item codes that commonly answer "leadership change" / "M&A" /
# "material agreement" questions — surfaced so the model doesn't have to
# guess what a code means.
_SEC_ITEM_LABELS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "5.02": "Departure/Election of Directors or Officers (leadership change)",
    "5.03": "Amendments to Articles of Incorporation/Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}


async def get_sec_full_text_search(
    query: str, ticker: str | None = None, form_types: str | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"q": query}
    if form_types:
        params["forms"] = form_types

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if ticker:
                ticker_map = await _load_sec_ticker_map(client)
                cik = ticker_map.get(ticker.upper())
                if cik is None:
                    return {"error": f"No SEC EDGAR record found for {ticker.upper()} — may not be a US-listed company."}
                params["ciks"] = cik

            resp = await client.get(
                "https://efts.sec.gov/LATEST/search-index", params=params, headers=_SEC_HEADERS
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
    except (httpx.HTTPError, httpx.TimeoutException):
        return {"error": "Couldn't reach SEC EDGAR full-text search right now."}

    if not hits:
        return {"query": query, "results": [], "note": "No matching filings found."}

    results = []
    for hit in hits[:10]:
        source = hit.get("_source", {})
        accession_no_dashes = hit.get("_id", "").split(":")[0].replace("-", "")
        primary_doc = hit.get("_id", "").split(":")[-1] if ":" in hit.get("_id", "") else ""
        cik_for_url = (source.get("ciks") or [""])[0].lstrip("0") or "0"
        items = source.get("items") or []
        results.append(
            {
                "company": (source.get("display_names") or [None])[0],
                "form": source.get("form"),
                "filing_date": source.get("file_date"),
                "items": items,
                "item_meanings": [_SEC_ITEM_LABELS.get(i) for i in items if i in _SEC_ITEM_LABELS],
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik_for_url}/{accession_no_dashes}/{primary_doc}",
            }
        )
    return {"query": query, "results": results}


async def get_economic_indicator(series_id: str) -> dict[str, Any]:
    if not FRED_API_KEY:
        return {"error": "FRED isn't configured (no API key set) — tell the user this data source isn't available yet."}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": FRED_API_KEY,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 6,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException):
        return {"error": f"Couldn't fetch FRED series {series_id} right now."}

    observations = data.get("observations")
    if not observations:
        return {"error": f"No data found for FRED series '{series_id}' — check the series ID."}
    return {
        "series_id": series_id,
        "recent_observations": [{"date": o["date"], "value": o["value"]} for o in observations],
    }


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
    "get_insider_transactions": get_insider_transactions,
    "get_financial_ratios": get_financial_ratios,
    "get_analyst_ratings": get_analyst_ratings,
    "get_sec_filings": get_sec_filings,
    "get_sec_full_text_search": get_sec_full_text_search,
    "get_economic_indicator": get_economic_indicator,
    # "read_sheet": read_sheet,           -> bound in agent.py
    # "save_memory_fact"                  -> bound in agent.py (needs user_id/db)
}
