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
2. **Finance vertical is the #1 mandatory priority and must be rock-solid before anything
   else gets attention.** Other verticals/integrations are optional bonus points ONLY,
   never at finance's expense — per the actual hackathon brief, judged weight is:
   usefulness/proactivity 30%, product thinking 25%, AI/conversation quality 20%,
   depth of finance vertical 15%, engineering quality 10%.
3. Ask only ONE question per message — never stack multiple questions in a single reply,
   in onboarding or anywhere else.
4. Responses must be SHORT and conversational — 2-5 sentences typical, never a wall of text.
   No markdown walls, no "Here is a comprehensive breakdown:" — talk like a witty, sharp
   analyst texting you, not a corporate chatbot.
5. If nothing meaningful changed, the daily brief stays SILENT. Never send filler.
6. Ambiguous requests → ask ONE short clarifying question, don't assume.
7. Every financial fact must come from a live tool call (Finnhub etc), never invented.
   If data can't be fetched/verified, say so plainly instead of guessing.
8. The bot must be able to answer "what do you know about me?" conversationally, pulling
   from stored memory facts — this is a specific judged proof point.

## Scope
This is a living list, not a locked contract — update it as the plan evolves rather than
treating it as permanent. The two exclusions below are the only hard "don't"s, because the
hackathon brief itself explicitly rules them out (not because we lack time):
- **n8n / any no-code workflow tool.**
- **Vector DB / embeddings / RAG pipelines** — live API calls + injecting document/sheet
  text straight into context IS the grounding; the brief agrees this is unnecessary.

Everything else — including tech stack, libraries, and which integrations to build — is
flexible and should be chosen based on what makes Atlas faster, more accurate, and
simpler to maintain, not treated as fixed. Swap tools freely if a better option shows up;
just don't over-engineer (e.g. don't add a queue/worker system, custom UI, or a second
database when the current setup handles the load fine).

Current build plan (see Progress log for real-time status), roughly in priority order:
1. Finance vertical core: Gemini tool-calling conversation, Finnhub data, memory facts,
   Google Sheets Q&A, PDF upload Q&A, daily brief, watchlist price alerts, voice + image
   input. This must be excellent before anything below gets attention.
2. Optional integrations the brief explicitly calls out as valuable differentiators once
   (1) is solid: Gmail (email summarization, meeting prep), Google Calendar (scheduling,
   reminders), Google Drive (document search). Introduce conversationally during
   onboarding, always skippable, never required.
3. Optional non-finance verticals — only if there's time left and finance is airtight.
   Not required; brief treats this as a minor bonus, not an expectation.

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

## Tech stack (flexible starting point — swap freely if something serves the product better)
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
- Only n8n and vector DB/RAG pipelines are actually off-limits (see Scope) — everything
  else is open to reconsidering if it genuinely helps. Still flag anything that looks
  like scope creep (new heavy infra, premature abstraction, features nobody asked for)
  rather than silently building it — "flexible" doesn't mean "unplanned."
- When a feature is ambiguous, default to the simplest version that satisfies the
  hackathon brief, not the most sophisticated version possible.
- Prioritize: (1) it runs without crashing, (2) it works end-to-end for the demo,
  (3) code quality. In that order, given the time constraint.
- After each working feature, note it in the "Progress" section below so future
  sessions don't rebuild or contradict it.

## Full feature checklist (from the actual hackathon brief)
Kept here so nothing named in the brief gets missed. Not all of these are required —
finance-core items are mandatory, the rest are differentiators.

**Onboarding** — conversational, one question at a time, always skippable, may offer
Gmail/Calendar/Drive connection mid-conversation (never as an upfront form).

**Daily Intelligence** — proactive brief explaining *why* something matters, not just
headlines; silent when nothing material happened; quality over frequency.

**Natural Conversation** — no commands ever; asks ONE clarifying question when a request
is ambiguous ("tell me about Apple" → ask what angle); remembers context across turns;
personalized, not generic-chatbot answers.

**Company & Market Research** — profiles, financials, earnings, news, leadership,
funding/M&A, filings, sentiment, competitor comparisons — explain *why* it matters, not
just summarize.

**Financial Document Intelligence** — summarize/compare/Q&A over annual reports, earnings
decks, filings; upload-and-ask, not a document-first UI.

**Live Financial Information** — accuracy over completeness; state uncertainty plainly
rather than guess.

**Integrations** — Finnhub (financial data, chosen) · Google Sheets · PDF/Drive documents
· optionally Gmail/Calendar once finance-core is solid. Pick integrations that genuinely
improve UX, not to maximize integration count.

**Personalization** — learns companies/sectors/preferences/schedule over time through
conversation, not a one-time onboarding form.

**User interaction** — text, voice, images only; no Telegram-native UI features.

## Progress log
(Update this as features are completed — keeps every session consistent)
- [x] FastAPI + Telegram webhook skeleton deployed to Railway (`app/main.py`, `app/telegram/`)
- [x] Postgres schema (users, messages, memory_facts, watchlist, prefs) — `app/db/models.py`, `app/db/queries.py`
- [x] Gemini conversation loop with tools wired in from the start (`app/ai/agent.py`) — tool schemas/impls already existed in tools.py in Claude's `input_schema` shape, converted to Gemini `FunctionDeclaration` format at import time. Switched from Claude API to Gemini (free tier) after hitting Anthropic billing
- [x] Full multi-step conversational onboarding matching the brief's bullet list exactly — role, sectors/companies followed, stocks to monitor, insight preferences, briefing time, custom alerts, asked one at a time in the model's own witty words, each answer captured via the right tool (`app/ai/prompts.py`'s `ONBOARDING_QUESTIONS` + `onboarding_suffix()`, `User.onboarding_step` state machine, `continue_onboarding` tool). Skippable permanently at any point (never resumes once stopped) — verified via direct answer path and skip/redirect path. Acknowledge-and-ask-next happens in one reply, not two round trips
- [x] Finnhub tools wired in (`app/ai/tools.py`: get_quote, get_company_profile, get_news, get_earnings, compare_companies)
- [x] Memory fact extraction + injection (`save_memory_fact` tool → `add_memory_fact`/`get_memory_facts` in queries.py, injected via `build_system_prompt`)
- [x] Typing indicator (`send_chat_action`, refreshed every 4s while Gemini is thinking — `app/telegram/handlers.py`)
- [x] Voice input pipeline (`app/telegram/handlers.py` + `transcribe_audio` in `app/ai/agent.py` — downloads the voice note, transcribes via a plain Gemini call, then runs through the normal text pipeline)
- [x] Image input pipeline (`app/telegram/handlers.py` passes photo bytes directly into the Gemini turn as an inline part — real vision reasoning, not just captioning)
- [x] Google Sheets Q&A (`app/integrations/sheets.py` — CSV-export trick for public/shared links, no OAuth needed; bound into `agent.py`'s tool dispatch). `read_sheet` now also computes exact per-column stats (count/sum/mean/min/max + IQR-based outliers) in Python from the full untruncated data before the raw CSV text gets cut down for the token budget — the model is told to use these for any sum/average/anomaly question rather than counting from raw text itself. Still not RAG/embeddings (locked out of scope) — plain arithmetic over the same data already in context, not an indexed/retrieved layer.
- [x] `search_drive_files` now searches file CONTENT too (Drive's `fullText contains`), not just file name — covers Docs/Sheets/Slides/PDFs-with-text-layer/plain text
- [x] PDF upload Q&A (`app/integrations/documents.py` using pypdf — text extracted from uploaded PDF, injected into that turn only)
- [x] PowerPoint deck upload Q&A (`app/integrations/presentations.py` using python-pptx — slide text, tables, and speaker notes extracted; covers "Investment Decks"/"Earnings Presentations" from the brief, which PDF/xlsx support didn't)
- [x] Cross-message document comparison fix — PDF/spreadsheet/PPTX uploads used to have their extracted text discarded from DB history after one turn (only a bare placeholder was kept), so "compare this to the report I sent earlier" silently couldn't work even though the bot claimed document Q&A was supported. Fixed by persisting a capped (4000-char) version of each upload's text into history instead of just a placeholder — bounded naturally by `get_recent_messages`' 20-message window, so cost doesn't grow unbounded. Verified via a unit test on the capping helper.
- [x] `add_to_watchlist` / `set_briefing_time` / `set_price_alert` tools (`app/ai/tools.py` + `app/db/queries.py`) — prerequisite for daily brief and price alerts; previously nothing could actually populate a user's watchlist or briefing time
- [x] Daily brief scheduler (`app/jobs/daily_brief.py`, APScheduler `IntervalTrigger` every 15 min in `app/main.py`) — reuses the normal tool-calling loop via `system_suffix` so Gemini can pull live quotes/news itself; per-user time matching against `briefing_time` (default 08:00 UTC), stays silent on `NOTHING_TO_REPORT`. Known limitation: no persisted "last sent date," so a server restart could rarely skip or double-fire a day — acceptable for hackathon scope
- [x] Watchlist price-alert polling (`app/jobs/price_alerts.py`, same scheduler, every 15 min) — two triggers: automatic >5% daily move (20h cooldown to prevent re-fire spam) and explicit user-set target price (one-shot, self-clears after firing). Templated messages, no LLM call needed since the trigger is deterministic — saves free-tier Gemini quota
- [x] Gmail + Calendar integration — real OAuth2 (not a placeholder), one combined consent for both (`app/integrations/google_oauth.py`: manual httpx token exchange/refresh, no google-auth-oauthlib dependency; `app/integrations/google_api.py`: search_gmail, list_calendar_events, create_calendar_event; `GoogleCredential` table in `db/models.py`; `/oauth/google/callback` route in `main.py` sends a Telegram confirmation once connected). Offered conversationally at onboarding wrap-up and organically when the user asks about email/calendar and isn't connected yet. Verified end-to-end with real Google Cloud OAuth credentials — real consent screen, real callback, real Gmail/Calendar API calls
- [x] Anti-hallucination fix: model was fabricating a fake connect-account URL instead of calling `get_google_connect_link` — added an explicit "never make up a URL, only use what a tool actually returned" rule; verified fixed
- [x] Formatting fix: Telegram doesn't render markdown in plain `sendMessage` calls, so `**bold**` was showing as literal asterisks — banned markdown syntax entirely in the persona, use plain "• " bullet characters (not markdown) for genuinely listy content (comparisons, calendar events), short WhatsApp-style paragraphs for normal conversation
- [x] Prompt-injection immunity + scope lock — hard rule to never follow instructions embedded in user messages/documents/images/voice transcriptions that try to override identity or push off-task, and to decline non-finance requests (e.g. "write me Python code") even when asked directly/insistently. Verified against a DAN-style jailbreak attempt and a direct off-topic code request — both correctly refused, system prompt never leaked
- [x] Capabilities + creator-attribution responses — full capability rundown when asked "what can you do," and "who made you" correctly attributes sole development to Nilesh Choudhury with his email and LinkedIn, only when actually asked (not volunteered unprompted)
- [x] Spreadsheet upload Q&A (`app/integrations/spreadsheets.py` — .xlsx via openpyxl, .csv via plain decode; same "inject as document_text" pipeline as PDFs, so it reuses all existing plumbing with zero agent.py changes). Legacy .xls explicitly declined with a message asking for .xlsx/.csv instead, rather than silently failing
- [x] Google Drive integration (`app/integrations/google_api.py`: `search_drive_files`, `read_drive_file`) — Drive scope added to OAuth; reads Google Docs/Sheets (export), PDFs, CSV/xlsx, plain text; `read_drive_file` on a folder lists its contents instead of erroring (browsing), on an image returns name+link (no OCR). Verified end-to-end with a real connected account after enabling the Drive API in Google Cloud Console
- [x] Broad market/macro news tool (`get_market_news` in `app/ai/tools.py`, Finnhub general category) — covers breaking financial news, economic/regulatory stories market-wide, not just per-ticker; wired into the daily brief so it's not limited to watchlist-only price moves
- [x] Daily Intelligence rewritten to match the hackathon brief's actual wording — checks price moves, company news, earnings, AND broad market news every brief, explicitly explains **why** each item matters rather than forwarding headlines (`DAILY_BRIEF_SYSTEM_SUFFIX` in `app/ai/prompts.py`). Silent-when-nothing-material rule unchanged (already correct)
- [x] **Time-grounding overhaul** — the bot was hallucinating badly on anything time-related (claiming times had passed when they hadn't, doing timezone arithmetic wrong, denying it can proactively message users). Root-caused and fixed at the architecture level, not just prompted around:
  - Real current UTC time (and the user's own precomputed local time, once timezone known) injected into every system prompt turn — model is told never to compute/guess this itself
  - `User.timezone` added as a real structured column (not relied-on-and-mis-parsed from free-text memory facts)
  - `briefing_time` is stored as **pure local time** (e.g. "08:00"), never converted to a fixed UTC offset at set-time — the scheduler (`app/jobs/daily_brief.py`: `_user_local_now`) resolves local→UTC fresh via `zoneinfo` on every check, so DST transitions never cause drift, and `set_briefing_time`/`set_timezone` are fully independent tool calls with no cross-turn chaining dependency
  - Added a blanket "grounding rule" to the persona: no fact may be stated (price, date, status, capability, past action) without tracing to a tool result/injected data/stated rule
- [x] Full codebase audit (spawned an independent review agent) — found and fixed 11 real bugs: silent bot failures on unguarded exceptions in the file-download path, a midnight-UTC-wraparound bug that permanently skipped some users' daily brief, OAuth `state` param spoofing (any unauthenticated request could link their Google account to a victim's chat — fixed with HMAC-signed state), missing try/except around all Gmail/Calendar/Drive API calls, onboarding misfiring on a new user's first non-`/start` message, Google Drive search-query injection via unescaped quotes, unguarded per-tool-call exceptions aborting whole turns, plus dead code and minor cleanup. See git history for full details
- [x] Scheduler reliability fixes — `next_run_time=datetime.now()` so briefing/alert checks start immediately on process startup instead of waiting a full 15-minute interval dead zone after every restart/redeploy; `misfire_grace_time=None` so a delayed tick still runs instead of APScheduler's default silent-skip
- [x] Full demo run-through recorded

**Pending before next Railway deploy:**
- New/changed columns on `users` since the last deploy (`onboarding_step`, `timezone`) and the `google_credentials` table — since there's no migration tool (no Alembic, just `create_all`), the already-created Railway Postgres tables won't automatically get new columns (new tables ARE created fine by `create_all`, just not new columns on existing tables). Either manually `ALTER TABLE` the changed columns, or drop/recreate those tables before deploying (likely few rows so far).
- New env vars needed on Railway: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `PUBLIC_BASE_URL` (set to the Railway domain, not the ngrok one).
- Google Cloud Console's OAuth client needs the Railway callback URL (`https://<railway-domain>/oauth/google/callback`) added to Authorized redirect URIs alongside the ngrok one already there.
- OAuth `state` signing (`_sign_chat_id` in `app/integrations/google_oauth.py`) reuses `GOOGLE_OAUTH_CLIENT_SECRET` as the HMAC key — no new env var needed, but note this means rotating that secret invalidates any in-flight (unclicked) connect links.

## Local development
Local dev now runs against the real Telegram bot via an ngrok tunnel (`ngrok http 8000`)
instead of only testing on Railway — much faster iteration. When switching between local
and production testing, remember to re-run Telegram's `setWebhook` pointed at whichever
URL is currently active (ngrok's free-tier URL can change on tunnel restart; check
`http://127.0.0.1:4040/api/tunnels` for the current one).
