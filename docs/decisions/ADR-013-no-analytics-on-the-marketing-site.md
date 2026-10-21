# ADR-013 — the marketing site carries no analytics

- **Status:** Accepted
- **Date:** 2026-10-21
- **Relates to:** ADR-010 (v1 is local-first), `docs/FRONTENDS.md`, `website/`

---

## Context

The website launch checklist includes "Analytics (Google Analytics)". Installing
it is the default advice for a marketing site, and for most products it is
correct.

It is wrong for **this** product, for three reasons that are not stylistic:

1. **It contradicts the product's central claim.** The site's own pages say
   *"no telemetry"* and *"the data never leaves your machine"*, and the product
   exists because the alternatives — ChatGPT, Julius AI, Power BI Pro — are
   cloud services that do exactly this. Loading a Google Analytics script puts
   the visitor's IP address, a cookie and a persistent identifier into a
   third-party company on every page view. The site would be the one part of
   the product that does not keep the promise.
2. **It is a legal liability, not a feature.** A visitor's IP is personal
   data under GDPR. Analytics therefore requires a consent banner, a cookie
   policy, a consent-management platform, a withdrawal flow and a data
   processing agreement with Google. For a product whose differentiation is
   *"we don't do that"*, this is a large, permanent, non-technical cost.
3. **It buys the wrong metric.** The number that matters is *downloads of the
   installer*, and the site already links to the GitHub Release, which
   reports download counts natively — with no script, no cookie, no consent
   banner and no third party. The metric is available for free and with
   strictly less data movement.

## Decision

**The marketing site loads no analytics and sets no cookies.** No Google
Analytics, no Plausible, no Fathom, no Vercel Analytics, no beacon, no
fingerprinting.

The only third-party script the site may load is Razorpay's checkout, in
`website/js/billing.js`, and only when a buyer clicks "pay" on a configured
billing server. That is a deliberate, user-initiated payment hand-off to a
payment processor the buyer has already chosen, and it is the one case where
the third party is unavoidable.

The site already loads **no third-party script and no third-party font**:
GSAP and the four webfont families are vendored under `website/vendor/`, so
the only external origin a page view touches is the site's own host.

## Consequences

- **The measurement question is answered differently.** Use the GitHub
  Release download counts for installer downloads, and a self-hosted,
  cookieless, aggregate-only counter if the marketing site itself ever needs
  traffic figures. A self-hosted counter keeps the data in the operator's own
  logs; it is not a third party and it sets no cookie.
- **There is no cookie banner** — nothing to consent to. The site's privacy
  page states plainly that it sets no cookies and runs no trackers, which is
  a *stronger* trust signal than a banner that asks permission to track.
- **The GDPR exposure of the marketing site drops to "the hosting provider's
  server logs"**, which every site has and which the privacy page should
  mention honestly rather than deny.
- **`tests/test_launch_site_privacy.py`** and
  `tests/test_launch_site_launch_readiness.py` fail the build if a new
  third-party origin appears, so this decision cannot be quietly undone by a
  later change.
- If the operator later decides they want traffic data, this ADR is the
  place to reverse it — and the decision to reverse it should be taken
  knowing it puts a tracker on the site of a privacy product.
