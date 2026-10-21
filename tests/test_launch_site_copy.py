"""Marketing-site copy guards — the reconciled launch claims, mechanically.

The launch remediation pass reconciled a set of facts that the marketing site
must never contradict again:

* the product is **local-first** (a local desktop app), not "100% local";
* there is **one price list, in INR** — Free (₹0), Pro (₹499/mo), Lifetime
  (₹10,000) — and the site and the app must agree, digit for digit;
* **no plan has unlimited AI**; every plan states a daily request cap;
* the feature names are *Regression Explorer*, *Segment Breakdown* and
  *Trend Forecast* — not AutoML / Segmentation / Forecasting;
* the download links come from ONE constant, ``window.DATAAGENT_REPO_URL``
  in ``website/js/main.js``;
* the PDF export is the **basic** one;
* "open source" means **AGPL-3.0**.

On top of that this file guards the mechanical side of the copy pass:
broken word joins, emoji in prose, ALL-CAPS headings, placeholder titles and
meta descriptions, and third-party script/style origins.

The price checks read the real plan table out of ``api/subscriptions.py`` (the
app is the authority) instead of hard-coding the numbers, so a future price
change cannot leave the site quietly stale.

Two assertions are deliberately **not** site-wide, and say so in their
docstring: the "self-hosting unlocks everything" claim and the third-party
origin allowlist.  Both are scoped to the pages this copy pass owns (or to a
fixed allowlist) because the three pages owned by another agent still carry
the old wording; see the agent report.

Run with::

    python -m pytest tests/test_launch_site_copy.py -q -p no:cacheprovider
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "website"
SITE_JS = WEBSITE / "js"

#: The pages this file reads. ``website/*.html`` is the marketing site; the
#: checkout script is part of the pricing surface.
PAGES = sorted(WEBSITE.glob("*.html"))
SCRIPTS = sorted(SITE_JS.glob("*.js"))
PRICING_SCRIPTS = [SITE_JS / "billing.js"]

#: Pages whose copy this pass owns. Site-wide assertions use every page;
#: assertions about *this* copy use only these.
OWNED_PAGES = [
    WEBSITE / "about.html",
    WEBSITE / "faq.html",
    WEBSITE / "docs.html",
    WEBSITE / "blog.html",
    WEBSITE / "blog-post.html",
    WEBSITE / "changelog.html",
    WEBSITE / "contact.html",
    WEBSITE / "security.html",
    WEBSITE / "billing.html",
    WEBSITE / "download.html",
    WEBSITE / "usecases.html",
]

# ==========================================================================
# helpers
# ==========================================================================


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_text(path: Path) -> str:
    """The file as a reader sees it: HTML entities resolved.

    The pages write prices as ``&#8377;499`` as often as ``₹499``; both must
    be found by the price and prose checks.
    """
    import html as _html

    return _html.unescape(_read(path))


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:  # pragma: no cover - defensive
        return str(path)


def _site_files() -> list[Path]:
    return PAGES + SCRIPTS


def _strip_code(markup: str) -> str:
    """Remove script/style/comment blocks so only prose is inspected."""
    markup = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    return markup


def _text_nodes(markup: str) -> list[str]:
    """The visible text of every element, one entry per text node."""
    import html as _html

    markup = _strip_code(markup)
    out = []
    for m in re.finditer(r">([^<>]+)<", markup):
        out.append(_html.unescape(m.group(1)))
    return out


def _app_plans() -> dict:
    """The plan table the running app uses (``api/subscriptions.py``).

    Parsed with ``ast`` (never ``import``) so the test does not pull the
    FastAPI app in — but the numbers are the app's, not a copy of them.
    """
    source = _read(ROOT / "api" / "subscriptions.py")
    tree = ast.parse(source)
    namespace: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not names or names[0] not in {"PLANS", "CURRENCY"}:
            continue
        code = compile(
            ast.Module(body=[node], type_ignores=[]), "<subscriptions>", "exec"
        )
        exec(code, namespace)  # noqa: S102 - literal assignments only
    assert "PLANS" in namespace, (
        "api/subscriptions.py no longer defines PLANS — the site copy test "
        "cannot check the prices against the app any more"
    )
    return namespace["PLANS"]


def _rupee(amount: int) -> str:
    """The site writes 10000 as ₹10,000."""
    return "\u20b9" + f"{amount:,}"


# ==========================================================================
# the reconciled claims
# ==========================================================================

#: Phrases that belong to the retired price list or to the retired
#: "unlimited AI" model. Compared case-insensitively.
BANNED_PHRASES = (
    "$6/mo",
    "$120",
    "$60/yr",
    "unlimited chat",
    "unlimited ai",
    "unlimited ai queries",
)

#: The retired feature labels. These are *user-facing* names; the API paths
#: and the Python module names are unchanged and are not the site's business.
RETIRED_FEATURE_LABELS = ("AutoML", "Segmentation", "Forecasting")


def test_no_retired_price_or_unlimited_ai_claim_anywhere():
    offenders: list[str] = []
    for path in _site_files():
        low = _read_text(path).lower()
        for phrase in BANNED_PHRASES:
            if phrase.lower() in low:
                offenders.append(f"{_rel(path)}: {phrase!r}")
    assert not offenders, (
        "the site quotes a price list or an AI allowance that the app does "
        "not have. The only price list is INR \u20b90 / \u20b9499 per month / "
        "\u20b910,000 one-time, and every plan has a daily request cap. "
        "Offenders: " + "; ".join(offenders)
    )


def test_no_retired_feature_label_is_used():
    offenders: list[str] = []
    for path in _site_files():
        text = _read(path)
        for label in RETIRED_FEATURE_LABELS:
            if label in text:
                offenders.append(f"{_rel(path)}: {label!r}")
        if "automl" in text.lower():
            offenders.append(f"{_rel(path)}: 'automl' (case-insensitive)")
    assert not offenders, (
        "these feature labels promise more than the code delivers (there is "
        "no model search, no clustering and no ARIMA/ETS). Use 'Regression "
        "Explorer', 'Segment Breakdown' and 'Trend Forecast'. Offenders: "
        + "; ".join(offenders)
    )


def test_the_release_url_lives_in_exactly_one_constant():
    """Every download/repository link derives from ``website/js/main.js``."""
    main_js = _read(SITE_JS / "main.js")
    assert main_js.count("github.com/DataAgent") == 1, (
        "website/js/main.js must hold the release base URL exactly once"
    )
    offenders = [
        _rel(p)
        for p in _site_files()
        if p != (SITE_JS / "main.js") and "github.com/DataAgent" in _read(p)
    ]
    assert not offenders, (
        "a page hardcodes the GitHub repository instead of using the "
        "data-download / data-repo-link markers. Offenders: "
        + ", ".join(offenders)
    )


def test_the_site_prices_agree_with_the_app_plan_table():
    """The site and the app must never quote different numbers."""
    plans = _app_plans()
    assert set(plans) == {"free", "pro", "lifetime"}, (
        f"unexpected plan set in api/subscriptions.py: {sorted(plans)}"
    )

    billing_js = _read(SITE_JS / "billing.js")
    billing_html = _read_text(WEBSITE / "billing.html")

    expected = {
        "free": (plans["free"]["price"], "MONTHLY"),
        "pro": (plans["pro"]["price"], "MONTHLY"),
        "lifetime": (plans["lifetime"]["price"], "ONETIME"),
    }

    for code, (amount, cycle) in expected.items():
        # The free tier is not billed, so it carries no "/mo" suffix even
        # though the plan table marks its cycle as MONTHLY.
        suffix = "/mo" if (cycle == "MONTHLY" and int(amount) > 0) else ""
        needle = f"price: '{_rupee(amount)}{suffix}'"
        assert needle in billing_js, (
            f"website/js/billing.js must show the {code} plan as {needle!r} — "
            f"that is what api/subscriptions.py charges"
        )
        assert _rupee(amount) in billing_html, (
            f"website/billing.html must show the {code} price {_rupee(amount)!r}"
        )

    # Every plan states its real daily AI allowance; none of them is unlimited.
    for code, plan in plans.items():
        cap = int(plan["ai_requests_daily"])
        assert cap > 0, f"plan {code!r} has no daily AI cap"
        assert f"{cap} AI requests a day" in billing_js, (
            f"website/js/billing.js must state the {code} allowance as "
            f"{cap!r} AI requests a day"
        )

    assert "recommended: true" in billing_js, (
        "Lifetime is the headline plan and must stay marked as recommended"
    )


def test_the_pricing_surfaces_quote_no_other_amount():
    """One price list: every rupee amount on the pricing pages is a plan price."""
    plans = _app_plans()
    allowed = {int(p["price"]) for p in plans.values()}
    offenders: list[str] = []
    for path in [WEBSITE / "billing.html", *PRICING_SCRIPTS]:
        text = _read_text(path)
        for m in re.finditer(r"\u20b9\s?([0-9][0-9,]*)", text):
            digits = m.group(1).replace(",", "")
            if int(digits) not in allowed:
                offenders.append(f"{_rel(path)}: {_rupee(int(digits))}")
    assert not offenders, (
        "the pricing pages quote an amount that is not one of the three plan "
        f"prices ({sorted(allowed)}). Offenders: " + "; ".join(offenders)
    )


def test_no_dollar_denominated_price_anywhere():
    """The single price list is in INR — a dollar amount is a second list."""
    offenders: list[str] = []
    for path in _site_files():
        text = _read_text(path)
        for m in re.finditer(r"\$\s?[0-9]", text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{_rel(path)}:{line}: {m.group(0)!r}")
    assert not offenders, (
        "the site quotes a dollar amount; the only price list is in rupees "
        "(₹0 / ₹499 per month / ₹10,000). Offenders: "
        + "; ".join(offenders)
    )


def test_self_hosting_is_not_promised_to_unlock_every_paid_feature():
    """Self-hosting removes the checkout, not the plan gates.

    The free plan is still the free plan in a self-hosted build: the AI cap
    and the paid feature gates come from the plan, and the only way to change
    the plan locally is an admin grant (``POST /subscriptions/{user_id}``).

    The check covers the pages this copy pass owns. ``website/comparison.html``
    still carries the old claim and is owned by another agent.
    """
    pattern = re.compile(
        r"(?:everything|every feature|all features|the whole product"
        r"|the full product)[^.;]{0,60}?free forever"
        r"|free forever[^.;]{0,60}?(?:everything|every feature|all features"
        r"|the whole product|the full product)",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in [*OWNED_PAGES, *PRICING_SCRIPTS]:
        text = _read_text(path)
        for m in pattern.finditer(text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{_rel(path)}:{line}: {m.group(0)!r}")
    assert not offenders, (
        "self-hosting does not unlock the paid features — the plan still "
        "decides the AI cap and the feature gates, and an admin has to grant "
        "a plan locally. Say what self-hosting actually removes (the "
        "checkout and the licence fee). Offenders: " + "; ".join(offenders)
    )


# ==========================================================================
# mechanics
# ==========================================================================


def test_every_page_has_a_title_and_a_real_meta_description():
    offenders: list[str] = []
    placeholders = ("lorem ipsum", "todo", "tbd", "coming soon", "xxx", "…")
    for path in PAGES:
        markup = _read(path)
        title = re.search(r"<title[^>]*>(.*?)</title>", markup, re.S | re.I)
        if not title or not title.group(1).strip():
            offenders.append(f"{_rel(path)}: missing or empty <title>")
        meta = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
            markup,
            re.S | re.I,
        )
        if not meta or not meta.group(1).strip():
            offenders.append(f"{_rel(path)}: missing or empty meta description")
            continue
        description = meta.group(1).strip()
        if description.lower() in placeholders or len(description) < 40:
            offenders.append(
                f"{_rel(path)}: meta description looks like a placeholder "
                f"({description!r})"
            )
    assert not offenders, (
        "every page needs a real <title> and a real meta description: "
        + "; ".join(offenders)
    )


#: Brand and technical names that legitimately contain a lower-to-upper
#: transition. A transition that is NOT one of these is a missing space.
KNOWN_MIXED_CASE = {
    "dataagent",
    "duckdb",
    "fastapi",
    "github",
    "pyinstaller",
    "openai",
    "openrouter",
    "anthropic",
    "chatgpt",
    "deepseek",
    "gemini",
    "javascript",
    "llama",
    "openid",
    "postgresql",
    "mysql",
    "sqlite",
    "smartscreen",
    "xai",
    "autosum",
    "opencode",
    "jobid",
    "powerbi",
    "pytorch",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "okta",
    "keycloak",
    "azure",
    "saml",
    "oidc",
    "jwt",
    "rbac",
    "rls",
    "sso",
    "mcp",
    "etl",
    "llm",
    "llms",
    "ai",
    "gpu",
    "cpu",
    "ram",
    "ssd",
    "hdd",
    "os",
    "faq",
    "faqs",
    "agpl",
    "mit",
    "owasp",
    "gdpr",
    "dpa",
    "saas",
    "rest",
    "http",
    "https",
    "api",
    "apis",
    "sql",
    "csv",
    "json",
    "jsonl",
    "ndjson",
    "pdf",
    "xlsx",
    "xls",
    "pptx",
    "parquet",
    "tsv",
    "upi",
    "roi",
    "sla",
    "mvp",
    "b2b",
    "b2c",
    "sqlite",
    "webview",
    "edge",
    "inno",
    "setup",
    "data",
    "agent",
    "studio",
    "explorer",
    "breakdown",
    "forecast",
    "pro",
    "free",
    "lifetime",
    "plus",
    "pro",
    "powerpoint",
    "macos",
    "libreoffice",
    "qoq",
    "yoy",
    "mom",
    "selfhosted",
    "smartscreen",
    "webview2",
    "inno",
    "setup",
    "admin",
    "okta",
    "keycloak",
    "azuread",
    "sqlite",
    "pysqlite",
    "psycopg",
    "pymysql",
    "openpyxl",
    "xlsxwriter",
    "plotly",
    "d3",
    "gsap",
    "lenis",
    "three",
}


def test_no_broken_word_joins_in_the_page_text():
    """A lower-case letter straight into an upper-case one inside a text node
    is a missing space ("Exploreanything", "Chartstitle", ...)."""
    offenders: list[str] = []
    for path in PAGES:
        for node in _text_nodes(_read(path)):
            for m in re.finditer(r"[a-z][A-Z]", node):
                start = m.start()
                while start > 0 and (node[start - 1].isalnum() or node[start - 1] in "_-'"):
                    start -= 1
                end = m.end()
                while end < len(node) and (node[end].isalnum() or node[end] in "_-'"):
                    end += 1
                token = node[start:end]
                if token.lower() in KNOWN_MIXED_CASE:
                    continue
                if "-" in token or "." in token:
                    # file names, version numbers, URLs
                    continue
                offenders.append(f"{_rel(path)}: {token!r}")
    assert not offenders, (
        "these words look like two words glued together (missing space): "
        + "; ".join(sorted(set(offenders)))
    )


def test_no_emoji_in_prose():
    """Emoji are fine inside icon markup; they are not fine in sentences."""
    import html as _html

    emoji = re.compile(
        "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\uFE0F]"
    )
    offenders: list[str] = []
    for path in OWNED_PAGES:
        markup = _strip_code(_read(path))
        # drop every element whose class mentions an icon
        markup = re.sub(
            r"<[a-z]+[^>]*class=[\"'][^\"']*icon[^\"']*[\"'][^>]*>.*?</[a-z]+>",
            " ",
            markup,
            flags=re.S | re.I,
        )
        for m in re.finditer(r"<(p|li|h[1-6])\b[^>]*>(.*?)</\1>", markup, flags=re.S | re.I):
            text = _html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            for em in emoji.finditer(text):
                offenders.append(
                    f"{_rel(path)}: {em.group(0)!r} in "
                    f"{re.sub(chr(92) + 's+', ' ', text).strip()[:80]!r}"
                )
    assert not offenders, (
        "emoji do not belong in sentences — they read as filler on a pricing "
        "or docs page: " + "; ".join(sorted(set(offenders)))
    )


def test_no_all_caps_heading_that_is_not_an_acronym():
    acronyms = {
        "SSO", "RBAC", "RLS", "API", "APIs", "ETL", "BI", "LLM", "LLMs",
        "AGPL", "SQL", "CSV", "JSON", "PDF", "GPU", "CPU", "RAM", "AI",
        "MCP", "JWT", "SAML", "OIDC", "GDPR", "DPA", "SaaS", "MIT", "OK",
        "FAQ", "FAQs", "UPI", "ROI", "SLA", "MVP", "B2B", "B2C", "OS",
        "HTTP", "HTTPS", "REST", "JSONL", "NDJSON", "XLSX", "PPTX", "SQLite",
    }
    import html as _html

    offenders: list[str] = []
    for path in PAGES:
        markup = _read(path)
        for m in re.finditer(r"<h([1-6])\b[^>]*>(.*?)</h\1>", markup, flags=re.S | re.I):
            text = _html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            text = re.sub(r"\s+", " ", text).strip()
            words = [w for w in re.findall(r"[A-Za-z0-9]+", text) if w]
            letters = [c for c in text if c.isalpha()]
            if not letters or not all(c.isupper() for c in letters):
                continue
            if all(w in acronyms for w in words):
                continue
            offenders.append(f"{_rel(path)}: {text!r}")
    assert not offenders, (
        "a heading shouted in capitals reads as filler; use sentence case "
        "unless every word is an acronym: " + "; ".join(offenders)
    )


# ==========================================================================
# third-party requests
# ==========================================================================

#: The only external origins the site is allowed to talk to. Both are
#: pre-existing: GSAP is loaded from jsDelivr and the web fonts from Google.
#: Anything else — a tracker, an analytics beacon, a new CDN — fails here.
# The site loads NO third-party script and NO third-party font any more:
# GSAP and the four webfont families are vendored under website/vendor/.
# The only external origin left is Razorpay's checkout, injected by
# website/js/billing.js on a click (see ADR-013).
ALLOWED_EXTERNAL_ORIGINS = {
    "checkout.razorpay.com",  # the payment hand-off, on click only
}

#: Hostnames that would mean the site started tracking its visitors.
TRACKING_MARKERS = (
    "google-analytics",
    "googletagmanager",
    "gtag",
    "analytics.js",
    "doubleclick",
    "googlesyndication",
    "adservice",
    "facebook.net",
    "connect.facebook",
    "hotjar",
    "clarity.ms",
    "segment.io",
    "cdn.segment",
    "mixpanel",
    "posthog",
    "plausible.io",
    "umami.is",
    "matomo",
    "sentry.io",
    "bugsnag",
    "intercom.io",
    "stripe.com",
    "fullstory",
    "heap.io",
)


def test_no_third_party_script_or_style_origin_beyond_the_allowlist():
    offenders: list[str] = []
    for path in PAGES:
        markup = _read(path)
        for m in re.finditer(
            r'<(?:script[^>]*\ssrc|link[^>]*\srel=["\']stylesheet["\'][^>]*\shref)'
            r'=["\']([^"\']+)["\']',
            markup,
            re.I,
        ):
            url = m.group(1)
            if url.startswith(("http://", "https://", "//")):
                origin = url.split("//", 1)[1].split("/")[0]
                if origin not in ALLOWED_EXTERNAL_ORIGINS:
                    offenders.append(f"{_rel(path)}: {url}")
            elif url.startswith(("http:", "https:")):
                offenders.append(f"{_rel(path)}: {url}")
    assert not offenders, (
        "the site must not add a third-party script or stylesheet beyond the "
        "allowed origins (" + ", ".join(sorted(ALLOWED_EXTERNAL_ORIGINS))
        + "). Offenders: " + "; ".join(offenders)
    )


def test_no_tracking_or_analytics_script():
    offenders: list[str] = []
    for path in PAGES:
        low = _read(path).lower()
        for marker in TRACKING_MARKERS:
            if marker in low:
                offenders.append(f"{_rel(path)}: {marker!r}")
    assert not offenders, (
        "the site must not track its visitors — no analytics, no beacons, no "
        "tag managers. Offenders: " + "; ".join(offenders)
    )


# ==========================================================================
# copy-to-destination
# ==========================================================================


def test_the_free_plan_cta_points_at_the_download():
    """There is no signup page: the download is the action."""
    billing_js = _read(SITE_JS / "billing.js")
    assert "download.html" in billing_js, (
        "the Free plan's call to action must send the visitor to the "
        "download page — there is no signup or account page on this site"
    )
    assert re.search(r"Download the free app", billing_js), (
        "the Free plan's button must say what happens next"
    )


def test_the_free_tier_states_its_real_allowance():
    """The free tier is 25 AI requests a day — say the number."""
    plans = _app_plans()
    cap = int(plans["free"]["ai_requests_daily"])
    for path in (WEBSITE / "billing.html", WEBSITE / "faq.html"):
        text = _read_text(path)
        assert f"{cap} AI requests a day" in text, (
            f"{_rel(path)} must state the free tier's allowance "
            f"({cap} AI requests a day) — a vague 'a daily AI-request cap' "
            "is not enough for a buyer comparing plans"
        )
