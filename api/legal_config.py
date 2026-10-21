"""Legal / company details for the hosted deployment.

The static pages under ``web/legal/`` are filled from ``config/legal.yaml``.
A value that is still a ``[[MARKER]]`` is a placeholder: the pages must not
publish it, so the marker is replaced with a clearly visible notice and the
operator is told at startup which markers remain.

Keeping the values in one file means the privacy policy, the terms, the DPA
and the security page cannot drift apart.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

#: Root of the repository (this file is ``<root>/api/legal_config.py``).
ROOT = Path(__file__).resolve().parent.parent

#: Where the operator's values live.
LEGAL_CONFIG_PATH = ROOT / "config" / "legal.yaml"

#: The marker shape used for an unfilled value.
MARKER_RE = re.compile(r"^\[\[[A-Z0-9_]+\]\]$")

#: Shown in place of an unfilled marker.
PLACEHOLDER_NOTICE = "[to be completed by the operator]"


def _load_yaml() -> Dict[str, Any]:
    try:
        import yaml

        with open(LEGAL_CONFIG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read %s: %s", LEGAL_CONFIG_PATH, exc)
        return {}


@lru_cache(maxsize=1)
def _flattened() -> Dict[str, str]:
    """Return the legal values as a flat ``{key: value}`` mapping."""
    data = _load_yaml()
    flat: Dict[str, str] = {}
    for section, values in data.items():
        if isinstance(values, dict):
            for key, value in values.items():
                flat[f"{section}.{key}"] = "" if value is None else str(value)
        else:
            flat[str(section)] = "" if values is None else str(values)
    return flat


def legal_values() -> Dict[str, str]:
    """Return the legal values with every unfilled marker replaced."""
    out: Dict[str, str] = {}
    for key, value in _flattened().items():
        if not value or MARKER_RE.match(value.strip()):
            out[key] = PLACEHOLDER_NOTICE
        else:
            out[key] = value
    return out


def unfilled_legal_markers() -> list:
    """Return the names of the values that are still placeholders."""
    return sorted(
        key for key, value in _flattened().items()
        if not value or MARKER_RE.match(value.strip())
    )


#: The phrase every "the operator must fill this in" value carries.  A value
#: that still carries it is *configured* — it is not an unfilled marker — but
#: it is not ready to publish either, so the release gate counts it.
NEEDS_OPERATOR_VALUE = "before public distribution"


def legal_values_needing_an_operator() -> list:
    """Return the keys whose value is a "fill this in before you publish" notice.

    These are not ``[[MARKER]]`` placeholders any more (the file carries no
    marker), so ``unfilled_legal_markers()`` cannot see them — but the legal
    pages still publish prose like *"set it in config/legal.yaml before public
    distribution"*, which is not a finished document.  The release gate uses
    this list to fail the check.
    """
    return sorted(
        key for key, value in _flattened().items()
        if value and NEEDS_OPERATOR_VALUE in value
    )


def render_legal_page(markdown: str) -> str:
    """Replace ``{{key}}`` placeholders in *markdown* with the legal values.

    An unknown key is left in place rather than silently blanked, so a typo
    in a page is visible instead of silently dropping content.
    """
    values = legal_values()

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return re.sub(r"\{\{([a-z_]+\.[a-z_]+)\}\}", _sub, markdown)
