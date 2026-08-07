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

from app.ai.prompts import build_system_prompt
from app.ai.tools import TOOL_DISPATCH, TOOL_SCHEMAS
from app.config import settings
from app.db.models import User
from app.db.queries import (
    add_memory_fact,
    get_memory_facts,
    get_recent_messages,
    get_user_prefs,
    get_watchlist,
)

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


async def handle_user_message(db: AsyncSession, user: User, user_text: str) -> str:
    prefs = await get_user_prefs(user)
    watchlist = await get_watchlist(db, user.id)
    facts = await get_memory_facts(db, user.id)
    history = await get_recent_messages(db, user.id, limit=20)

    system_prompt = build_system_prompt(prefs, watchlist, facts)

    contents = _history_to_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

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
            result = await _dispatch_tool(fc.name, dict(fc.args), user, db)
            response_parts.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )
        contents.append(types.Content(role="user", parts=response_parts))

    return "That took more steps than expected — mind rephrasing your question?"
