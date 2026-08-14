"""
telegram/handlers.py

Routes an incoming Telegram update to the right handling path: text, voice
notes (transcribed to text via Gemini, then handled like text), photos
(passed to Gemini vision directly, alongside any caption), and uploaded
documents — PDFs, spreadsheets (.xlsx/.csv), and PowerPoint decks (.pptx) —
text extracted, then handled like text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import handle_user_message, transcribe_audio
from app.ai.prompts import onboarding_suffix
from app.db.queries import get_or_create_user, get_recent_messages, save_message
from app.integrations.documents import extract_pdf_text
from app.integrations.presentations import extract_pptx_text
from app.integrations.spreadsheets import extract_spreadsheet_text
from app.telegram.client import download_file, send_message, send_typing_action

LEGACY_EXCEL_MIME = "application/vnd.ms-excel"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# How much of an uploaded document's text gets kept in conversation HISTORY
# (not just the current turn) — this is what lets "compare this to the
# report I sent earlier" actually work across separate messages, since
# get_recent_messages naturally bounds how many past documents accumulate
# (older ones roll off after ~20 messages). Smaller than the per-turn
# extraction cap (MAX_PDF_CHARS etc.) since this cost gets paid on every
# later turn until it rolls off, not just once.
MAX_HISTORY_DOC_CHARS = 4_000

logger = logging.getLogger(__name__)


def _doc_db_text(label: str, file_name: str, caption: str, text: str) -> str:
    snippet = text[:MAX_HISTORY_DOC_CHARS]
    if len(text) > MAX_HISTORY_DOC_CHARS:
        snippet += "\n[...truncated for history; full document was used for the initial reply...]"
    return f"[Uploaded {label}: {file_name}] {caption}\n\n{snippet}"

# Telegram's typing indicator only lasts ~5s per call, so it needs
# refreshing while a longer Gemini turn (e.g. multi-round tool calls) runs.
TYPING_REFRESH_SECONDS = 4


async def _keep_typing(chat_id: int) -> None:
    while True:
        await send_typing_action(chat_id)
        await asyncio.sleep(TYPING_REFRESH_SECONDS)


class ResolvedMessage(NamedTuple):
    agent_text: str  # full text sent to Gemini for this turn
    db_text: str  # short placeholder saved to conversation history
    image: tuple[bytes, str] | None = None
    document_text: str | None = None


async def _resolve_incoming(message: dict[str, Any], chat_id: int) -> ResolvedMessage | None:
    """Turns a Telegram message into a ResolvedMessage, or None if unsupported."""
    if "voice" in message:
        audio_bytes = await download_file(message["voice"]["file_id"])
        if audio_bytes is None:
            await send_message(chat_id, "Couldn't download that voice note — mind trying again?")
            return None
        mime_type = message["voice"].get("mime_type", "audio/ogg")
        text = await transcribe_audio(audio_bytes, mime_type)
        return ResolvedMessage(agent_text=text, db_text=text)

    if "photo" in message:
        largest = message["photo"][-1]
        image_bytes = await download_file(largest["file_id"])
        if image_bytes is None:
            await send_message(chat_id, "Couldn't download that image — mind trying again?")
            return None
        caption = message.get("caption") or "What do you see in this image?"
        return ResolvedMessage(agent_text=caption, db_text=caption, image=(image_bytes, "image/jpeg"))

    if "document" in message:
        doc = message["document"]
        mime_type = doc.get("mime_type", "")
        file_name = doc.get("file_name", "document")
        caption = message.get("caption") or "Summarize this document."

        if mime_type == LEGACY_EXCEL_MIME:
            await send_message(
                chat_id, "I can read .xlsx or .csv spreadsheets — could you re-save/export it as one of those?"
            )
            return None

        if mime_type == "application/pdf":
            file_bytes = await download_file(doc["file_id"])
            if file_bytes is None:
                await send_message(chat_id, "Couldn't download that PDF — mind trying again?")
                return None
            text = extract_pdf_text(file_bytes)
            if text is None:
                await send_message(chat_id, "Couldn't extract any text from that PDF — is it a scanned image?")
                return None
            return ResolvedMessage(
                agent_text=caption,
                db_text=_doc_db_text("PDF", file_name, caption, text),
                document_text=text,
            )

        if mime_type in ("text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
            file_bytes = await download_file(doc["file_id"])
            if file_bytes is None:
                await send_message(chat_id, "Couldn't download that spreadsheet — mind trying again?")
                return None
            text = extract_spreadsheet_text(file_bytes, mime_type)
            if text is None:
                await send_message(chat_id, "Couldn't read that spreadsheet — is it empty or corrupted?")
                return None
            return ResolvedMessage(
                agent_text=caption,
                db_text=_doc_db_text("spreadsheet", file_name, caption, text),
                document_text=text,
            )

        if mime_type == PPTX_MIME:
            file_bytes = await download_file(doc["file_id"])
            if file_bytes is None:
                await send_message(chat_id, "Couldn't download that presentation — mind trying again?")
                return None
            text = extract_pptx_text(file_bytes)
            if text is None:
                await send_message(chat_id, "Couldn't extract any text from that deck — is it image-only slides?")
                return None
            return ResolvedMessage(
                agent_text=caption,
                db_text=_doc_db_text("presentation", file_name, caption, text),
                document_text=text,
            )

        if mime_type.startswith("image/"):
            # An image sent via Telegram's "send as file" option arrives as
            # a document, not a photo — route it through the same vision
            # pipeline as the photo branch above rather than rejecting it;
            # this was previously falling through to the generic
            # unsupported-type message despite images being fully supported.
            image_bytes = await download_file(doc["file_id"])
            if image_bytes is None:
                await send_message(chat_id, "Couldn't download that image — mind trying again?")
                return None
            image_caption = message.get("caption") or "What do you see in this image?"
            return ResolvedMessage(
                agent_text=image_caption, db_text=image_caption, image=(image_bytes, mime_type)
            )

        await send_message(chat_id, "I can read PDFs, spreadsheets (.xlsx/.csv), PowerPoint decks (.pptx), and images for now.")
        return None

    if "text" in message:
        return ResolvedMessage(agent_text=message["text"], db_text=message["text"])

    return None


async def handle_update(update: dict[str, Any], db: AsyncSession) -> None:
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]

    # Everything below can hit the network (Telegram file downloads, DB
    # queries, Gemini) — wrapped from here so a failure anywhere in this
    # path still gets the user a reply instead of the bot going silent.
    typing_task = asyncio.create_task(_keep_typing(chat_id))
    try:
        user = await get_or_create_user(db, chat_id)

        resolved = await _resolve_incoming(message, chat_id)
        if resolved is None:
            known_types = ("text", "voice", "photo", "document")
            if not any(t in message for t in known_types):
                await send_message(
                    chat_id,
                    "I can only read text, voice notes, photos, PDFs, spreadsheets, and presentations for now.",
                )
            return

        is_plain_text = resolved.image is None and resolved.document_text is None
        is_start_command = is_plain_text and resolved.agent_text.strip() in ("/start", "start")
        # A user with onboarding_step=0 and literally no prior messages has
        # never actually been asked question 0 yet — treat their first
        # message as the onboarding kickoff regardless of exact text, not
        # just on a literal "/start", so it's never mistaken for an answer
        # to a question that was never asked.
        is_first_ever_message = not await get_recent_messages(db, user.id, limit=1)

        if (is_start_command or is_first_ever_message) and user.onboarding_step is not None:
            # Kick off (or re-ask, if they /start again mid-onboarding) the
            # current question — nothing to evaluate yet, so this trigger
            # text isn't saved as a real user message.
            reply = await handle_user_message(
                db, user, "/start",
                system_suffix=onboarding_suffix(user.onboarding_step, expecting_answer=False),
            )
        else:
            await save_message(db, user.id, "user", resolved.db_text)
            suffix = (
                onboarding_suffix(user.onboarding_step, expecting_answer=True)
                if user.onboarding_step is not None
                else None
            )
            reply = await handle_user_message(
                db,
                user,
                resolved.agent_text,
                image=resolved.image,
                document_text=resolved.document_text,
                system_suffix=suffix,
            )
        await save_message(db, user.id, "assistant", reply)
    except Exception:
        logger.exception("Failed to handle update for chat_id=%s", chat_id)
        reply = "Something went wrong on my end — mind trying that again?"
    finally:
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

    await send_message(chat_id, reply)
