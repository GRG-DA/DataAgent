"""Serve the legal pages with the operator's details filled in.

The pages under ``web/legal/`` are static HTML that carry ``[[MARKER]]``
placeholders.  Rendering them through a route (rather than serving the
raw file) means the company name, contact details, jurisdiction and
subprocessor list come from a single source — ``config/legal.yaml`` —
so the privacy policy, the terms, the DPA and the security page cannot
drift apart.

A marker that is still unfilled is replaced with a clearly visible
notice, never published as ``[[COMPANY_NAME]]``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from api.legal_config import PLACEHOLDER_NOTICE, legal_values

logger = logging.getLogger(__name__)

router = APIRouter(tags=["legal"])

#: Root of the repository (this file is ``<root>/api/routes/legal.py``).
ROOT = Path(__file__).resolve().parent.parent.parent
LEGAL_DIR = ROOT / "web" / "legal"

#: Only these page names are served — no path traversal.
PAGES = ("privacy", "terms", "dpa", "security")

_MARKER_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")


def _fill(markup: str) -> str:
    """Replace every ``[[MARKER]]`` with its configured value."""
    values = legal_values()
    # The config keys are "section.key"; the pages use the bare marker name.
    lookup = {key.split(".", 1)[-1]: value for key, value in values.items()}

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        return lookup.get(name, PLACEHOLDER_NOTICE)

    return _MARKER_RE.sub(_sub, markup)


@router.get("/legal/{page}.html", response_class=HTMLResponse, include_in_schema=False)
async def legal_page(page: str) -> HTMLResponse:
    """Serve one of the legal pages with the operator's details applied."""
    name = (page or "").strip().lower()
    if name not in PAGES:
        raise HTTPException(status_code=404, detail="Unknown legal page")
    path = LEGAL_DIR / f"{name}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Legal page not found")
    try:
        markup = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.error("could not read the legal page %s: %s", name, exc)
        raise HTTPException(status_code=500, detail="Legal page unavailable") from exc
    return HTMLResponse(
        content=_fill(markup),
        headers={"Cache-Control": "public, max-age=300"},
    )
