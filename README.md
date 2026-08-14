# Atlas — AI Financial Assistant

Atlas is a Telegram-native financial analyst. No commands, no menus, no buttons — you text it (or send a voice note, or a photo, or a PDF) the way you'd text a sharp colleague who happens to have live market data, your calendar, your inbox, and a memory of everything you've told it.

Built for the Humanity Founders "Atlas AI Financial Assistant Hackathon."

**Live bot:** message it on Telegram — no source code required to evaluate, per the hackathon's own submission rules. This README exists anyway, because a serious product deserves a serious README.

---

## What it actually does

Not a chatbot wrapper. Not a news forwarder. Every claim below is backed by a real tool call against a real data source — there is a hard architectural rule (see [Zero-hallucination architecture](#zero-hallucination-architecture)) that the bot is never allowed to state a fact it can't trace to a live source.

### Conversational onboarding, not a form
Six questions, one at a time, in the bot's own words, always skippable, never resumed once skipped. Learns your role, what you follow, what you want tracked, your insight preferences, your briefing schedule + timezone, and any custom alerts — through conversation, not a Typeform.

### Proactive daily intelligence
One quality-over-frequency daily brief per user, at their own local time. Checks price moves, company news, earnings, and broad market/regulatory news every time — explains **why** something matters, never forwards a bare headline — and stays completely silent if nothing material happened. No filler, ever.

Beyond the daily cadence:
- **Watchlist news alerts** — pushed the moment fresh news breaks on a tracked ticker, not held for the next brief
- **Price-move alerts** — automatic 5% daily-move trigger, or a custom percentage per ticker
- **Target-price alerts** — one-shot, self-clearing
- **One-off reminders** — "remind me an hour before Apple's earnings call," resolved against real fetched earnings-calendar data, DST-safe

### Real financial data, from real sources
| Source | What it covers |
|---|---|
| **Finnhub** | Live quotes, company news, earnings (past + next scheduled report), company profiles, financial ratios (P/E, margins, ROE, debt/equity, 52-week range), insider transactions, analyst rating trends, broad market news |
| **SEC EDGAR** | Real filing history (10-K/10-Q/8-K, etc.) and full-text search across actual filing content — including item-code tagging (e.g. `5.02` = leadership change, `1.01`/`2.01` = M&A) |
| **FRED** | Macroeconomic indicators — CPI, unemployment, Fed funds rate, GDP, Treasury yields |

If a data source isn't configured or a query comes back empty, the bot says so — it never fills the gap with training-data guesses.

### Financial document intelligence
Upload a PDF, spreadsheet (.xlsx/.csv), or PowerPoint deck and just ask about it — annual reports, earnings decks, investment decks, due-diligence docs. Documents stay usable across the conversation (not just the one turn you uploaded in), so "compare this to the report I sent earlier" actually works.

Spreadsheet math is computed exactly in Python (count/sum/mean/min/max, IQR-based outlier detection) from the full data before any truncation — not eyeballed by the model from raw text.

### Google Workspace, connected conversationally
One OAuth consent for Gmail + Calendar + Drive. Offered naturally during onboarding and organically later — never a forced step, always skippable, and the bot stops re-offering after being declined twice so it never nags. Once connected: email search, calendar scheduling, and Drive search that covers file **content**, not just filenames.

Google Sheets works without any connection at all — paste a shared link and it reads it directly.

### Personalization that compounds
Every system prompt is assembled fresh from stored role, watchlist, timezone, briefing time, and memory facts — not a bolted-on "smart memory" layer. The bot notices recurring topics across conversations and proactively saves them, and a stated reading-style preference ("I always want more detail") becomes the new default instead of being ignored after that one turn.

### Zero-hallucination architecture
The single rule the entire system prompt is built around: before stating *any* specific fact — a price, a date, whether something's connected, what a file contains, what the bot can or can't do — it must trace to a tool result from that turn, data explicitly injected into the prompt, or a stated rule. No source, no claim. This applies to system self-knowledge (scheduling, capabilities) exactly as much as financial facts.

Current date/time is injected as authoritative data every turn — the model is explicitly told never to compute or guess it. Timezone conversions happen in Python (`zoneinfo`), never as LLM arithmetic, after this exact failure mode caused real production incidents earlier in development.

### Text, voice, and images — nothing else
No slash commands, no inline buttons, no menus. Voice notes get transcribed and handled like text. Images work whether sent as a compressed "Photo" or as a raw file attachment. That's the entire interaction surface, by design.

---

## Architecture

```
Telegram (webhook) → FastAPI
                        ├─ Gemini 3.1 Flash Lite (tool-calling) — the conversational brain
                        │    27 tools: market data, SEC/FRED, Sheets/Drive/Gmail/Calendar,
                        │    memory, watchlist, reminders, onboarding state
                        ├─ PostgreSQL (Alembic-migrated) — users, messages, memory facts,
                        │    watchlist, reminders, Google credentials
                        └─ APScheduler — daily brief · price alerts · watchlist news ·
                             reminder polling, all running in-process
```

One system prompt per turn, built dynamically from stored prefs + watchlist + top memory facts + real current time. That assembly *is* the personalization — not a separate retrieval system.

Deliberately **not** using a vector DB, embeddings, or RAG — live tool calls plus injected document/sheet text are the grounding. Deliberately **not** using n8n or any no-code layer. Both are explicit hackathon-brief exclusions, not oversights.

## Tech stack

- **Backend:** Python 3.11+, FastAPI, fully async
- **Database:** PostgreSQL (Railway managed), SQLAlchemy async ORM, Alembic migrations
- **AI:** Google Gemini (`gemini-3.1-flash-lite`), manual tool-calling loop via `google-genai`
- **Financial data:** Finnhub, SEC EDGAR (public, no key), FRED
- **Google integrations:** Gmail, Calendar, Drive, Sheets — OAuth2 via manual `httpx` token exchange, HMAC-signed state param
- **Documents:** `pypdf` (PDF), `openpyxl` (xlsx), `python-pptx` (PowerPoint)
- **Scheduler:** APScheduler, in-process
- **Deploy:** Railway, GitHub-auto-deploy on push to `main`

## Project structure

```
app/
  main.py              # FastAPI app, webhook + OAuth callback routes, scheduler wiring
  config.py             # env-driven settings
  telegram/
    client.py           # Telegram Bot API calls
    handlers.py          # message routing: text/voice/photo/document
  ai/
    agent.py             # Gemini conversation loop + tool dispatch
    tools.py              # tool schemas + implementations (Finnhub/SEC/FRED)
    prompts.py             # system prompt assembly, onboarding flow, persona rules
  integrations/
    finnhub.py / sheets.py / documents.py / presentations.py / spreadsheets.py
    google_oauth.py / google_api.py
  db/
    models.py / queries.py
  jobs/
    daily_brief.py / price_alerts.py / watchlist_news.py / reminders.py
migrations/              # Alembic
```

---

## A note on how this was built

This project was built end-to-end with Claude Code, with the developer directing product decisions, catching real production bugs from live testing, and pushing back hard whenever a fix looked like a shortcut instead of a real one. The commit history reflects genuine iteration — including a full production incident (a crash loop from a migration bug, root-caused and fixed live against the real database) rather than a clean, edited-after-the-fact story.

---

*Built by Nilesh Choudhury for the Atlas AI Financial Assistant Hackathon.*
