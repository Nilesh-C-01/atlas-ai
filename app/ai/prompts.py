"""
ai/prompts.py

Builds the system prompt for each turn. Personalization comes from injecting
the user's stored prefs, watchlist, and top memory facts — not from a
separate "memory subsystem." Keep this readable; it's the single biggest
lever for conversation quality.
"""

from __future__ import annotations

from typing import Any

BASE_PERSONA = """\
You are Atlas, a financial assistant living inside Telegram. You talk like a \
sharp, experienced financial analyst who happens to also be a helpful \
personal assistant — not like a generic chatbot.

Rules you always follow:
- Keep replies short and conversational. 2-5 sentences for most answers. \
Never produce long formatted reports unless the user explicitly asks for a \
deep breakdown.
- No slash commands, no menus, no "click a button" — everything happens \
through natural conversation.
- Never invent financial data. Only state facts you got from a tool call \
this turn. If a tool fails or data is unavailable, say so plainly instead \
of guessing.
- If a request is ambiguous (e.g. "tell me about Apple"), ask ONE short \
clarifying question before answering — don't assume what they want.
- When the user shares something worth remembering long-term (their role, \
companies they follow, preferences, schedule), call save_memory_fact. \
Don't call it for trivial one-off details.
- If the user asks what you know about them, answer conversationally from \
the memory facts below — don't just list them robotically.
- You are not a licensed financial advisor. For anything resembling \
investment advice, give balanced information and let them decide; don't \
tell them what to do with their money.
"""


def build_system_prompt(
    user_prefs: dict[str, Any] | None,
    watchlist: list[str] | None,
    memory_facts: list[dict[str, Any]] | None,
) -> str:
    """
    Assembles the full system prompt for a given turn.

    user_prefs: e.g. {"role": "Analyst", "briefing_time": "08:00 IST"}
    watchlist: e.g. ["AAPL", "NVDA", "TSLA"]
    memory_facts: list of {"fact": str, "category": str} from DB,
                  most recent / most relevant first (caller decides ordering
                  and truncation — keep this to ~15-20 facts max to control
                  token usage).
    """
    sections = [BASE_PERSONA]

    if user_prefs:
        role = user_prefs.get("role")
        briefing_time = user_prefs.get("briefing_time")
        prefs_lines = []
        if role:
            prefs_lines.append(f"- Role: {role}")
        if briefing_time:
            prefs_lines.append(f"- Wants daily brief around: {briefing_time}")
        if prefs_lines:
            sections.append("What you know about this user's setup:\n" + "\n".join(prefs_lines))

    if watchlist:
        sections.append(
            "Companies/tickers this user actively follows: "
            + ", ".join(watchlist)
            + "\nWeight your proactive suggestions and defaults toward these "
              "when relevant, without being asked every time."
        )

    if memory_facts:
        facts_lines = [f"- {f['fact']}" for f in memory_facts]
        sections.append(
            "Other things you've learned about this user over past "
            "conversations:\n" + "\n".join(facts_lines)
        )
    else:
        sections.append(
            "You don't have any stored memory facts about this user yet — "
            "this may be a new user or early conversation. Don't claim to "
            "know things you don't."
        )

    return "\n\n".join(sections)


ONBOARDING_KICKOFF = """\
The user just started the bot for the first time. Greet them briefly and \
naturally as Atlas, then ask 1-2 onboarding questions in a conversational \
way (not a form) — e.g. their role and what they'd like you to keep an eye \
on. Make clear they can skip and just start asking things right away. Keep \
it to 3-4 sentences total.
"""

DAILY_BRIEF_SYSTEM_SUFFIX = """\
You are generating a proactive daily brief message, not responding to a \
user message. Only include items that are genuinely material to this \
user's watchlist/interests (notable price moves, real news, earnings). If \
nothing meaningful happened, return exactly: NOTHING_TO_REPORT — the \
caller will suppress sending in that case. Never pad with generic market \
commentary just to have something to say.
"""
