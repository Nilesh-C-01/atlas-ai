"""
integrations/google_api.py

Gmail + Calendar tool implementations. Each function takes (db, user_id, ...)
since it needs a valid access token for that specific user — same pattern as
the other per-user tools (save_memory_fact, add_to_watchlist) that are
special-cased in agent.py's dispatch rather than living in the generic
TOOL_DISPATCH table.
"""

from __future__ import annotations

import datetime
from typing import Any

import httpx

from app.db.queries import get_google_credential, update_google_access_token
from app.integrations.documents import extract_pdf_text
from app.integrations.google_oauth import expiry_from_token_response, refresh_access_token
from app.integrations.spreadsheets import extract_spreadsheet_text

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"

MAX_DRIVE_FILE_CHARS = 12_000

# Google-native file types need the export endpoint (they have no raw bytes
# of their own); everything else is downloaded and run through the same
# extractors used for direct Telegram uploads.
GOOGLE_DOC_EXPORT_MIME = "text/plain"
GOOGLE_SHEET_EXPORT_MIME = "text/csv"

NOT_CONNECTED = {
    "error": "This user hasn't connected their Google account yet — offer to "
    "send them a connect link via get_google_connect_link if they want this."
}


def _escape_drive_query_literal(value: str) -> str:
    # Google Drive's query DSL requires backslash and single-quote to be
    # backslash-escaped inside string literals — unescaped input lets a
    # search term like "O'Brien" break the query syntax, or worse, let
    # arbitrary query clauses be injected.
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def get_valid_access_token(db, user_id: int) -> str | None:
    cred = await get_google_credential(db, user_id)
    if cred is None:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = cred.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)

    if expires_at > now + datetime.timedelta(minutes=2):
        return cred.access_token

    token_data = await refresh_access_token(cred.refresh_token)
    if token_data is None:
        return None
    new_expiry = expiry_from_token_response(token_data)
    await update_google_access_token(db, cred.id, token_data["access_token"], new_expiry)
    return token_data["access_token"]


async def search_gmail(db, user_id: int, query: str, max_results: int = 5) -> dict[str, Any]:
    token = await get_valid_access_token(db, user_id)
    if token is None:
        return NOT_CONNECTED

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            list_resp = await client.get(
                f"{GMAIL_BASE}/users/me/messages",
                headers=headers,
                params={"q": query, "maxResults": max_results},
            )
            if list_resp.status_code != 200:
                return {"error": "Couldn't search Gmail right now."}
            message_ids = [m["id"] for m in list_resp.json().get("messages", [])]

            if not message_ids:
                return {"results": [], "note": "No matching emails found."}

            results = []
            for msg_id in message_ids:
                msg_resp = await client.get(
                    f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                )
                if msg_resp.status_code != 200:
                    continue
                data = msg_resp.json()
                headers_list = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
                results.append(
                    {
                        "subject": headers_list.get("Subject", "(no subject)"),
                        "from": headers_list.get("From", "unknown"),
                        "date": headers_list.get("Date", "unknown"),
                        "snippet": data.get("snippet", ""),
                    }
                )
            return {"results": results}
    except httpx.HTTPError:
        return {"error": "Couldn't reach Gmail right now."}


async def list_calendar_events(db, user_id: int, max_results: int = 10) -> dict[str, Any]:
    token = await get_valid_access_token(db, user_id)
    if token is None:
        return NOT_CONNECTED

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{CALENDAR_BASE}/calendars/primary/events",
                headers=headers,
                params={
                    "timeMin": now,
                    "maxResults": max_results,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )
    except httpx.HTTPError:
        return {"error": "Couldn't reach Calendar right now."}
    if resp.status_code != 200:
        return {"error": "Couldn't fetch calendar events right now."}

    events = [
        {
            "summary": e.get("summary", "(no title)"),
            "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
            "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
        }
        for e in resp.json().get("items", [])
    ]
    return {"events": events}


async def create_calendar_event(
    db,
    user_id: int,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
) -> dict[str, Any]:
    token = await get_valid_access_token(db, user_id)
    if token is None:
        return NOT_CONNECTED

    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{CALENDAR_BASE}/calendars/primary/events", headers=headers, json=body
            )
    except httpx.HTTPError:
        return {"error": "Couldn't reach Calendar right now."}
    if resp.status_code not in (200, 201):
        return {"error": "Couldn't create that calendar event right now."}

    data = resp.json()
    return {"created": True, "event_link": data.get("htmlLink")}


async def search_drive_files(db, user_id: int, query: str, max_results: int = 5) -> dict[str, Any]:
    token = await get_valid_access_token(db, user_id)
    if token is None:
        return NOT_CONNECTED

    headers = {"Authorization": f"Bearer {token}"}
    safe_query = _escape_drive_query_literal(query)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{DRIVE_BASE}/files",
                headers=headers,
                params={
                    "q": f"name contains '{safe_query}' and trashed = false",
                    "pageSize": max_results,
                    "fields": "files(id,name,mimeType,webViewLink)",
                },
            )
    except httpx.HTTPError:
        return {"error": "Couldn't reach Drive right now."}
    if resp.status_code != 200:
        return {"error": "Couldn't search Drive right now."}

    files = resp.json().get("files", [])
    if not files:
        return {"results": [], "note": "No matching files found."}

    return {
        "results": [
            {"id": f["id"], "name": f["name"], "type": f["mimeType"], "link": f.get("webViewLink")}
            for f in files
        ]
    }


async def read_drive_file(db, user_id: int, file_id: str) -> dict[str, Any]:
    token = await get_valid_access_token(db, user_id)
    if token is None:
        return NOT_CONNECTED

    headers = {"Authorization": f"Bearer {token}"}
    safe_file_id = _escape_drive_query_literal(file_id)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            meta_resp = await client.get(
                f"{DRIVE_BASE}/files/{file_id}",
                headers=headers,
                params={"fields": "name,mimeType,webViewLink"},
            )
            if meta_resp.status_code != 200:
                return {"error": "Couldn't find that file — check the file_id from search_drive_files."}
            meta = meta_resp.json()
            mime_type = meta.get("mimeType", "")

            if mime_type == "application/vnd.google-apps.folder":
                list_resp = await client.get(
                    f"{DRIVE_BASE}/files",
                    headers=headers,
                    params={
                        "q": f"'{safe_file_id}' in parents and trashed = false",
                        "pageSize": 20,
                        "fields": "files(id,name,mimeType,webViewLink)",
                    },
                )
                if list_resp.status_code != 200:
                    return {"error": "Couldn't list that folder's contents."}
                items = list_resp.json().get("files", [])
                if not items:
                    return {"folder_contents": [], "note": "This folder is empty."}
                return {
                    "folder_contents": [
                        {"id": f["id"], "name": f["name"], "type": f["mimeType"], "link": f.get("webViewLink")}
                        for f in items
                    ]
                }

            if mime_type.startswith("image/"):
                return {
                    "note": (
                        "This is an image file, not a text document — can't extract "
                        "readable text from it. Here's its name and link so you can "
                        "still point the user to it."
                    ),
                    "name": meta.get("name"),
                    "link": meta.get("webViewLink"),
                }

            if mime_type == "application/vnd.google-apps.document":
                resp = await client.get(
                    f"{DRIVE_BASE}/files/{file_id}/export",
                    headers=headers,
                    params={"mimeType": GOOGLE_DOC_EXPORT_MIME},
                )
                text = resp.text if resp.status_code == 200 else None

            elif mime_type == "application/vnd.google-apps.spreadsheet":
                resp = await client.get(
                    f"{DRIVE_BASE}/files/{file_id}/export",
                    headers=headers,
                    params={"mimeType": GOOGLE_SHEET_EXPORT_MIME},
                )
                text = resp.text if resp.status_code == 200 else None

            elif mime_type in ("application/pdf",):
                resp = await client.get(f"{DRIVE_BASE}/files/{file_id}", headers=headers, params={"alt": "media"})
                text = extract_pdf_text(resp.content) if resp.status_code == 200 else None

            elif mime_type in (
                "text/csv",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ):
                resp = await client.get(f"{DRIVE_BASE}/files/{file_id}", headers=headers, params={"alt": "media"})
                text = extract_spreadsheet_text(resp.content, mime_type) if resp.status_code == 200 else None

            elif mime_type.startswith("text/"):
                resp = await client.get(f"{DRIVE_BASE}/files/{file_id}", headers=headers, params={"alt": "media"})
                text = resp.text if resp.status_code == 200 else None

            else:
                return {"error": f"Can't read files of type {mime_type} yet — try a Doc, Sheet, PDF, or text file."}
    except httpx.HTTPError:
        return {"error": "Couldn't reach Drive right now."}

    if text is None or not text.strip():
        return {"error": "Couldn't extract any readable text from that file."}

    return {"text": text[:MAX_DRIVE_FILE_CHARS]}
