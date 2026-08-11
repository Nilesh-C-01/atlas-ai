"""
integrations/sheets.py

Reads public/shared-link Google Sheets by hitting the CSV export endpoint —
no OAuth or service account needed, matching the "read-only, public/shared
links" scope in CLAUDE.md. The raw CSV text is handed back to Gemini as
context; that IS the grounding (no separate parsing/embedding layer).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
GID_RE = re.compile(r"[#&]gid=(\d+)")

MAX_CSV_CHARS = 12_000  # keep the tool result within a sane token budget


async def read_sheet(sheet_url: str) -> dict[str, Any]:
    match = SHEET_ID_RE.search(sheet_url)
    if not match:
        return {"error": "That doesn't look like a valid Google Sheets URL."}

    sheet_id = match.group(1)
    gid_match = GID_RE.search(sheet_url)
    gid = gid_match.group(1) if gid_match else "0"

    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(export_url)
        except httpx.HTTPError:
            return {"error": "Couldn't reach Google Sheets right now — try again shortly."}

    if resp.status_code == 404:
        return {"error": "Sheet not found — check the link is correct."}
    if resp.status_code != 200 or "text/html" in resp.headers.get("content-type", ""):
        return {
            "error": (
                "Couldn't read that sheet — make sure sharing is set to "
                "'Anyone with the link can view'."
            )
        }

    csv_text = resp.text
    truncated = len(csv_text) > MAX_CSV_CHARS
    if truncated:
        csv_text = csv_text[:MAX_CSV_CHARS]

    return {
        "csv_data": csv_text,
        "truncated": truncated,
    }
