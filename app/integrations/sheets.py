"""
integrations/sheets.py

Reads public/shared-link Google Sheets by hitting the CSV export endpoint —
no OAuth or service account needed, matching the "read-only, public/shared
links" scope in CLAUDE.md. The raw CSV text is handed back to Gemini as
context; that IS the grounding (no separate parsing/embedding layer).

Exact math (sums, averages, outlier detection) is computed here in Python
from the FULL data before any truncation, rather than left for the model
to eyeball from raw text — an LLM counting/summing a column from text is
unreliable, especially once MAX_CSV_CHARS cuts a large sheet off partway
through. This is still not a RAG/embeddings pipeline (locked out of scope):
it's plain arithmetic over the same data already being handed to the model,
computed once per call, not indexed/retrieved.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
from typing import Any

import httpx

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
GID_RE = re.compile(r"[#&]gid=(\d+)")

MAX_CSV_CHARS = 12_000  # keep the tool result within a sane token budget
MAX_COMPUTED_COLUMNS = 15  # avoid a huge stats blob on very wide sheets


def _compute_column_stats(csv_text: str) -> list[dict[str, Any]]:
    """Per numeric column: count/sum/mean/min/max plus outliers via Tukey's
    IQR method (value outside Q1-1.5*IQR .. Q3+1.5*IQR) — computed exactly,
    not estimated from truncated text. IQR rather than a z-score/stdev
    threshold specifically: a single extreme outlier inflates its own
    stdev enough to mask itself in small samples (verified against a
    5-row test case where a 50x-larger value came within 0.2% of a
    2-stdev threshold it should have cleared); IQR's quartiles aren't
    dragged around by the outlier itself the same way."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if len(rows) < 2:
        return []

    header, data_rows = rows[0], rows[1:]
    stats = []
    for col_idx, col_name in enumerate(header[:MAX_COMPUTED_COLUMNS]):
        values: list[float] = []
        for row in data_rows:
            if col_idx >= len(row):
                continue
            cell = row[col_idx].strip().replace(",", "").replace("$", "").replace("%", "")
            if not cell:
                continue
            try:
                values.append(float(cell))
            except ValueError:
                pass

        # Skip columns that aren't meaningfully numeric (e.g. mostly text,
        # or IDs where every row differs and stats would be noise).
        if len(values) < max(3, len(data_rows) // 2):
            continue

        mean = statistics.fmean(values)
        outliers: list[float] = []
        if len(values) >= 4:
            q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
            iqr = q3 - q1
            if iqr > 0:
                lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                outliers = sorted({v for v in values if v < lower or v > upper})

        stats.append(
            {
                "column": col_name,
                "count": len(values),
                "sum": round(sum(values), 4),
                "mean": round(mean, 4),
                "min": min(values),
                "max": max(values),
                "outliers_iqr": outliers[:10],
            }
        )
    return stats


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

    full_csv_text = resp.text
    computed_stats = _compute_column_stats(full_csv_text)

    csv_text = full_csv_text
    truncated = len(csv_text) > MAX_CSV_CHARS
    if truncated:
        csv_text = csv_text[:MAX_CSV_CHARS]

    return {
        "csv_data": csv_text,
        "truncated": truncated,
        "computed_stats": computed_stats,
    }
