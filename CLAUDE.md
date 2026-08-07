# Atlas — AI Financial Assistant (Telegram Bot)

## What this is
Hackathon submission for Humanity Founders ("Atlas AI Financial Assistant Hackathon").
Goal: a Telegram bot that feels like a personal financial analyst — natural conversation,
memory, proactive daily briefs, live financial data, document/sheet Q&A.

**Deadline:** Sunday EOD preferred, Monday 11:59 AM hard cutoff.
**Submission:** demo video + live Telegram bot link. NO source code required — so
correctness and demo-ability matter more than "impressive" architecture.

## Non-negotiable product rules
1. **No Telegram-native UI** — no slash commands, inline buttons, menus, quick replies.
   Every interaction is plain text/voice/image, handled conversationally.
2. **Finance vertical is the #1 and ONLY mandatory priority.** Do not add other verticals.
3. Responses must be SHORT and conversational — 2-5 sentences typical, never a wall of text.
   No markdown walls, no "Here is a comprehensive breakdown:" — talk like an analyst texting you.
4. If nothing meaningful changed, the daily brief stays SILENT. Never send filler.
5. Ambiguous requests → ask ONE short clarifying question, don't assume.
6. Every financial fact must come from a live tool call (Finnhub etc), never invented.
   If data can't be fetched/verified, say so plainly instead of guessing.
7. The bot must be able to answer "what do you know about me?" conversationally, pulling
   from stored memory facts — this is a specific judged proof point.

## Scope (locked — do not expand without explicit approval)
**IN:** Gemini tool-calling conversation · Finnhub (quote/news/earnings/profile/compare) ·
Google Sheets Q&A (paste link) · PDF upload Q&A · memory facts (DB, not vector/RAG) ·
scheduled daily brief (APScheduler) · simple watchlist price-alert polling ·
voice input (transcribe → text pipeline) · image input (Gemini vision).

**OUT (do not build unless finance-vertical is 100% solid first):**
Gmail/Calendar/Drive integration, Bloomberg/PitchBook/Crunchbase, multi-vertical support,
n8n or any no-code workflow tool, vector DB / embeddings / hybrid search (unnecessary —
live API calls + injecting document text into context IS the grounding), complex
real-time push infra (polling is fine), any custom UI beyond Telegram.

## Architecture
```
Telegram (webhook) → FastAPI
                        ├─ Gemini (3.1 Flash Lite, tool-calling) — the conversational brain
                        │    tools: get_quote, get_news, get_earnings, get_profile,
                        │           compare_companies, read_sheet, read_document,
                        │           save_memory_fact
                        ├─ Postgres — users, messages, memory_facts, watchlist, prefs
                        └─ APScheduler — daily_brief job, price_alert_poll job
```
One system prompt per turn, built dynamically from: user prefs + watchlist + top N
memory facts. This is what makes responses feel personalized — not a separate "smart"
memory system, just good prompt assembly.

## Tech stack (locked)
- Backend: Python 3.11+, FastAPI
- DB: PostgreSQL (Railway managed), SQLAlchemy (async) or raw asyncpg — keep it simple
- AI: Google Gemini API (3.1 Flash Lite, free tier), tool-calling via `google-genai` SDK —
  switched from Claude API to avoid Anthropic billing; model choice is explicitly not
  an evaluation criterion per the hackathon brief. Note: not every Gemini model has
  free quota on a given Google Cloud project — check the quota dashboard
  (AI Studio → usage/rate limits) before picking a model; `gemini-2.0-flash` had 0
  quota on this project while `gemini-3.1-flash-lite` had 500 req/day
- Financial data: Finnhub (free tier)
- Sheets: Google Sheets API v4 (read-only, public/shared links)
- PDF: pypdf or pdfplumber for text extraction
- Scheduler: APScheduler (in-process, no separate worker needed at this scale)
- Deploy: Railway
- Voice: Telegram voice note → OpenAI Whisper API or Gemini audio input (pick whichever ships faster)

## Coding conventions
- Modular structure, one concern per file. Suggested layout:
  ```
  app/
    main.py              # FastAPI app, webhook route
    config.py            # env vars, settings
    telegram/
      client.py          # send/receive helpers
      handlers.py        # message routing (text/voice/image)
    ai/
      agent.py           # Gemini conversation loop + tool dispatch
      tools.py           # tool schemas + implementations
      prompts.py         # system prompt builder
    integrations/
      finnhub.py
      sheets.py
      documents.py
    db/
      models.py
      queries.py
    jobs/
      daily_brief.py
      price_alerts.py
  ```
- Async throughout (FastAPI + async DB driver + async HTTP client for tool calls).
- Every external call (Telegram, Gemini, Finnhub, Sheets) wrapped in try/except with a
  graceful conversational fallback message — never let the bot go silent or throw a
  raw error at the user.
- Type hints everywhere. Pydantic models for structured data (tool inputs/outputs).
- No hardcoded secrets — all via env vars, `.env` for local dev (never committed).
- Keep functions short and single-purpose; favor clarity over cleverness — this code
  will not be reviewed by judges, but it needs to be debuggable by me under time pressure.

## Session behavior for Claude Code
- Always re-read this file at the start of a session before writing code.
- Do not introduce n8n, vector DBs, RAG pipelines, or new verticals — these have been
  explicitly rejected for this build. If asked to add something out of scope, flag it
  rather than silently building it.
- When a feature is ambiguous, default to the simplest version that satisfies the
  hackathon brief, not the most sophisticated version possible.
- Prioritize: (1) it runs without crashing, (2) it works end-to-end for the demo,
  (3) code quality. In that order, given the time constraint.
- After each working feature, note it in the "Progress" section below so future
  sessions don't rebuild or contradict it.

## Progress log
(Update this as features are completed — keeps every session consistent)
- [x] FastAPI + Telegram webhook skeleton built (`app/main.py`, `app/telegram/`) — not yet deployed to Railway
- [x] Postgres schema (users, messages, memory_facts, watchlist, prefs) — `app/db/models.py`, `app/db/queries.py`
- [x] Gemini conversation loop with tools wired in from the start (`app/ai/agent.py`) — tool schemas/impls already existed in tools.py in Claude's `input_schema` shape, converted to Gemini `FunctionDeclaration` format at import time. Switched from Claude API to Gemini (free tier) after hitting Anthropic billing — hackathon brief explicitly states model choice is not evaluated, any free/open model is fine
- [x] Conversational onboarding (`/start` routes into the agent with `ONBOARDING_KICKOFF` so Gemini generates the greeting, not a canned string)
- [x] Finnhub tools wired in (`app/ai/tools.py`: get_quote, get_company_profile, get_news, get_earnings, compare_companies)
- [x] Memory fact extraction + injection (`save_memory_fact` tool → `add_memory_fact`/`get_memory_facts` in queries.py, injected via `build_system_prompt`)
- [ ] Google Sheets tool (schema exists in tools.py, `read_sheet` impl not yet written — needs `app/integrations/sheets.py`)
- [ ] PDF upload Q&A
- [ ] Daily brief scheduler
- [ ] Price alert polling
- [ ] Voice input pipeline
- [ ] Image input pipeline
- [ ] Full demo run-through recorded
