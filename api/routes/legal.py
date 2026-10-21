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

#: The marker in a legal page -> the key in ``config/legal.yaml``.
#:
#: This table is explicit on purpose.  The pages use screaming-snake markers
#: (``[[COMPANY_NAME]]``) and the config uses dotted paths
#: (``company.name``), so the two do not share a naming scheme and cannot be
#: related mechanically.  Deriving the lookup from the config key's last
#: segment silently produced a lookup that matched NONE of the markers, so
#: every legal page published "[to be completed by the operator]" in all 38
#: places even with a fully filled config.
#:
#: Add a marker to a page -> add its key here.  ``tests/test_launch_legal_pages.py``
#: fails when a page uses a marker that is missing from this table.
MARKER_KEYS = {
    "COMPANY_NAME": "company.name",
    "COMPANY_ADDRESS": "company.address",
    "JURISDICTION": "company.jurisdiction",
    "CONTACT_EMAIL": "company.contact_email",
    "SECURITY_EMAIL": "company.security_email",
    "DPO_EMAIL": "company.dpo_email",
    "EFFECTIVE_DATE": "legal.effective_date",
    "DATA_REGION": "legal.data_region",
    "RETENTION_DAYS": "legal.retention_days",
    "SUBPROCESSOR_HOSTING": "subprocessors.hosting",
    "SUBPROCESSOR_STORAGE": "subprocessors.storage",
    "SUBPROCESSOR_EMAIL": "subprocessors.email",
    "SUBPROCESSOR_LLM": "subprocessors.llm",
    "SUBPROCESSOR_MONITORING": "subprocessors.monitoring",
}


def _fill(markup: str) -> str:
    """Replace every ``[[MARKER]]`` with its configured value."""
    values = legal_values()

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        key = MARKER_KEYS.get(name)
        if key is None:
            logger.warning(
                "legal page uses the marker [[%s]], which has no key in "
                "config/legal.yaml (see api/routes/legal.py::MARKER_KEYS)",
                name,
            )
            return PLACEHOLDER_NOTICE
        return values.get(key) or PLACEHOLDER_NOTICE

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
