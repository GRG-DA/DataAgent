# ADR-010 — v1 ships as a local-first desktop app, not a hosted SaaS

- **Status:** Accepted
- **Date:** 2026-10-21
- **Decides:** the product shape that the first commercial release is built and marketed around
- **Relates to:** ADR-009 (hosted multi-tenant SaaS), `docs/LAUNCH-READINESS.md`,
  `docs/LAUNCH-TECHNICAL-REVIEW.md`

---

## Context

Two documents in this repo described two different products.

`docs/LAUNCH-READINESS.md` concluded the moat is **local-first**: *"100% Local, No Telemetry,
Open Source, $0, no subscription, the data itself never leaves your machine."*

`docs/decisions/ADR-009` (2026-10-20) said the opposite: *"DataAgent becomes a hosted
multi-tenant SaaS. The data plane moves to the server."*

Both cannot be true, and the first question an enterprise buyer asks is *"where does my data
live?"*. Today the honest answer depends on which document you read.

The code review (`docs/LAUNCH-TECHNICAL-REVIEW.md`) then found that the hosted path is **not
ready**: six launch-blocking defects, each reachable by a default self-serve account — a sandbox
escape to remote code execution, an API-key path that never binds a tenant, `/ask` reading the
operator's schema, a cross-tenant race in the agent singleton, an SSRF to the cloud metadata
service, and exports reading the platform schema. The end-to-end tenant-isolation test that
ADR-009 claimed to have does not exist.

## Decision

**v1 ships as a local-first desktop application.** The Windows `.exe` is the product.

ADR-009 is **not** deleted. It is retained as the documented **v2** path — the hosted tier that
will be built after the local product is in market and the tenancy model has been rebuilt as
the product's spine rather than retrofitted onto it.

## Rationale

1. **The local-first story is the genuine moat.** ChatGPT and Julius AI are cloud-only. Power BI
   Desktop is local but cannot do NL → SQL → dashboard → `.pbix`. Nobody does the `.pbix` round
   trip well — not ChatGPT, not Julius AI, not the open-source BI tools. That is the gap, and
   the code is already at it: `export/pbix_export.py` (2,585 lines) writes a real Power BI
   Desktop file, and `api/pbix_spec.py` (475 lines) imports real `.pbix` files back.

2. **It eliminates the entire hosted-isolation risk class.** The six P0 defects in the technical
   review are all cross-tenant defects. A single-tenant local install has no tenant boundary to
   cross. Fixing the code but shipping the hosted product anyway would mean paying for a risk
   class the local product does not have.

3. **It costs ~$0 to serve.** A local install consumes no compute, no egress, no storage, no
   database. The gross margin on a one-time licence is ~95%.

4. **The code is built for it.** DuckDB on a local file, `data/` under `%LOCALAPPDATA%`, a
   single-writer model that needs no cluster. The hosted path is the retrofit; the local path is
   the original.

5. **The review economy is the business.** For a self-serve download product, a user's review
   decides the revenue. A product that says "your data never leaves your machine" and is
   genuinely true is defensible; a product that says it and is not is a refund queue.

## Consequences

### Accepted

- The primary product is a **one-time Lifetime licence**. The recurring subscription tier is
  either dropped or given a hard, metered AI cap — never "unlimited AI" at a price that cannot
  cover the inference cost. See `docs/BILLING.md`.
- Hosted deployment is **v2**, and when it ships it must carry: per-request tenant connections
  (never a pinned shared `search_path`), `tenant_id` on API keys, `analytics` added to
  `_BLOCKED_SCHEMAS` for non-default tenants, egress allow-listing, and a real end-to-end HTTP
  tenant-isolation test.
- The marketing site, the ADRs, the billing docs and the in-app upgrade screen must all tell the
  **same** story. A claim-consistency guard lives in `tests/test_launch_frontend_claims.py`.

### Rejected

- **Ship hosted now.** Six one-request compromises, no completed payment, no KYC, and an
  isolation test that only exercises helpers rather than the real endpoints.
- **Keep both stories.** "Where does my data live?" must have one answer.

### Neutral

- The single-writer DuckDB constraint, the local backup story and the recovery drill are still
  required — they are the local product's operational burden, not the hosted one's.

## Implementation checklist for v1

| # | Item | Status |
|---|---|---|
| 1 | The `.exe` must let a fresh user create their first account without an environment variable | required |
| 2 | The JWT secret and the grant key must persist in the writable per-user dir, not in the bundle | required |
| 3 | The build must be code-signed (SmartScreen "Unknown publisher" is a conversion killer) | required |
| 4 | The marketing site, ADRs and billing docs must state one consistent story | required |
| 5 | Feature names must describe what the code does ("AutoML" → "Regression Explorer") | required |
| 6 | The password-reset endpoint must not claim success when no mail was sent | required |
| 7 | A paid plan must survive an app restart (it is signed with a key that must persist) | required |

## Revisit when

The hosted tier is revisited only when all of the following hold: the local product has paying
customers, the P0 items in `docs/LAUNCH-TECHNICAL-REVIEW.md` are fixed and covered by an
end-to-end HTTP tenant-isolation test, and a real AI cost ceiling exists per tenant.
