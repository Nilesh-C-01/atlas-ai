"""
ai/agent.py

The conversation loop: build system prompt, call the model with tools,
execute any tool calls, feed results back, repeat until it gives a final
text reply. This is the only place that talks to the LLM API.

Uses Google Gemini (free tier) rather than the Claude API — swapped in to
avoid Anthropic API billing. Tool schemas in ai/tools.py are still written
in Claude's `input_schema` shape; they're converted to Gemini's
FunctionDeclaration format at import time below.
"""

from __future__ import annotations

import logging
from typing import Any

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import ONBOARDING_QUESTIONS, build_system_prompt
from app.ai.tools import TOOL_DISPATCH, TOOL_SCHEMAS
from app.config import settings
from app.db.models import User
from app.db.queries import (
    add_memory_fact,
    add_watchlist_item,
    advance_onboarding,
    get_memory_facts,
    get_recent_messages,
    get_user_prefs,
    get_watchlist,
    note_google_offer_declined,
    set_briefing_time_local,
    set_price_alert,
    set_user_timezone,
)
from app.integrations.google_api import (
    create_calendar_event,
    list_calendar_events,
    read_drive_file,
    search_drive_files,
    search_gmail,
)
from app.integrations.google_oauth import build_authorize_url
from app.integrations.sheets import read_sheet

TOOL_DISPATCH = {**TOOL_DISPATCH, "read_sheet": read_sheet}

client = genai.Client(api_key=settings.google_api_key)
MODEL = "gemini-3.1-flash-lite"
MAX_TOOL_ROUNDS = 5

logger = logging.getLogger(__name__)

GEMINI_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["input_schema"],
        )
        for t in TOOL_SCHEMAS
    ]
)


async def _dispatch_tool(name: str, tool_input: dict[str, Any], user: User, db: AsyncSession) -> Any:
    if name == "save_memory_fact":
        await add_memory_fact(db, user.id, tool_input["fact"], tool_input["category"])
        return {"saved": True}
    if name == "add_to_watchlist":
        await add_watchlist_item(db, user.id, tool_input["ticker"])
        return {"added": True}
    if name == "set_briefing_time":
        return await set_briefing_time_local(db, user.id, tool_input["local_time_24h"])
    if name == "set_timezone":
        return await set_user_timezone(db, user.id, tool_input["timezone"])
    if name == "set_price_alert":
        await set_price_alert(db, user.id, tool_input["ticker"], tool_input["target_price"])
        return {"set": True, "note": "this also added the ticker to their watchlist, if not already on it"}
    if name == "continue_onboarding":
        await advance_onboarding(db, user.id, tool_input["stop"], len(ONBOARDING_QUESTIONS))
        return {"acknowledged": True}
    if name == "get_google_connect_link":
        return {"link": build_authorize_url(user.telegram_chat_id)}
    if name == "note_google_offer_declined":
        return await note_google_offer_declined(db, user.id)
    if name == "search_gmail":
        return await search_gmail(db, user.id, tool_input["query"])
    if name == "list_calendar_events":
        return await list_calendar_events(db, user.id, tool_input.get("max_results", 10))
    if name == "create_calendar_event":
        return await create_calendar_event(
            db,
            user.id,
            tool_input["summary"],
            tool_input["start_iso"],
            tool_input["end_iso"],
            tool_input.get("description", ""),
        )
    if name == "search_drive_files":
        return await search_drive_files(db, user.id, tool_input["query"])
    if name == "read_drive_file":
        return await read_drive_file(db, user.id, tool_input["file_id"])
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name}"}
    return await fn(**tool_input)


def _history_to_contents(history) -> list[types.Content]:
    contents = []
    for m in history:
        role = "model" if m.role == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m.content)]))
    return contents


async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    """Plain (no-tools) Gemini call that turns a voice note into text."""
    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    types.Part.from_text(
                        text="Transcribe this voice message. Return only the "
                        "transcribed text, nothing else."
                    ),
                ],
            )
        ],
        config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=0)),
    )
    return response.text.strip()


async def handle_user_message(
    db: AsyncSession,
    user: User,
    user_text: str,
    image: tuple[bytes, str] | None = None,
    document_text: str | None = None,
    system_suffix: str | None = None,
) -> str:
    prefs = await get_user_prefs(user)
    watchlist = await get_watchlist(db, user.id)
    facts = await get_memory_facts(db, user.id)
    history = await get_recent_messages(db, user.id, limit=20)

    system_prompt = build_system_prompt(prefs, watchlist, facts)
    if system_suffix:
        system_prompt = f"{system_prompt}\n\n{system_suffix}"

    contents = _history_to_contents(history)
    turn_text = user_text
    if document_text is not None:
        # Full extracted text goes into this turn only — the DB history
        # keeps a short placeholder instead, so future turns don't replay
        # the whole document on every request.
        turn_text = f"[Attached document text]\n{document_text}\n\n[User's message]\n{user_text}"
    turn_parts = [types.Part.from_text(text=turn_text)]
    if image is not None:
        image_bytes, mime_type = image
        turn_parts.insert(0, types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    contents.append(types.Content(role="user", parts=turn_parts))

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[GEMINI_TOOLS],
        # Gemini 3's "thinking" mode attaches a thought_signature to function
        # call parts that must be replayed verbatim on the next turn. We manage
        # the tool loop manually rather than via the SDK's auto-calling helper,
        # so we don't preserve those signatures — disable thinking to avoid the
        # 400 INVALID_ARGUMENT this otherwise causes on multi-step tool calls.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )
        except Exception:
            logger.exception("Gemini API call failed")
            return "I'm having trouble reaching my brain right now — try again in a bit?"

        candidate = response.candidates[0]
        function_calls = [
            part.function_call for part in candidate.content.parts if part.function_call
        ]

        if not function_calls:
            text = "".join(
                part.text for part in candidate.content.parts if part.text
            ).strip()
            return text or "..."

        contents.append(candidate.content)

        response_parts = []
        for fc in function_calls:
            try:
                result = await _dispatch_tool(fc.name, dict(fc.args), user, db)
            except Exception:
                # A malformed/unexpected argument from the model (missing
                # required field, wrong type) shouldn't abort the whole
                # turn — tell the model the call failed so it can retry or
                # explain, instead of the user getting a generic "something
                # went wrong" for what's really just one bad tool call.
                logger.exception("Tool call failed: %s(%s)", fc.name, dict(fc.args))
                result = {"error": f"That {fc.name} call failed unexpectedly — check the arguments and try again."}
            response_parts.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )
        contents.append(types.Content(role="user", parts=response_parts))

    return "That took more steps than expected — mind rephrasing your question?"
