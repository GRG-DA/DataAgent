# ADR-023 — Publish the source under PolyForm Noncommercial 1.0.0

**Date:** 2026-11-03
**Status:** Accepted
**Relates to:** ADR-021 (keep the source private until it is published),
`docs/FRONTENDS.md`, `docs/marketing/claim-audit.md`,
`tests/test_launch_frontend_claims.py`

## Context

ADR-021 kept the source unpublished for the launch and left the licence
decision open. The owner has now decided to publish. The licence has to match
how the paid product is sold: a free tier and paid tiers of the packaged app
for personal and educational users, while the source itself is not offered for
commercial reuse.

## Decision

Publish the source publicly at `https://github.com/GRG-DA/DataAgent` on
2026-11-03 under PolyForm Noncommercial 1.0.0.

- The repository's `LICENSE` changes from GNU AGPL v3 to PolyForm
  Noncommercial 1.0.0: use, modification and distribution of the source are
  allowed for personal, educational and other noncommercial purposes;
  commercial or monetary use requires the owner's permission.
- PolyForm Noncommercial is not open source by the OSI definition. Marketing
  must never claim that DataAgent is open source. The no-unbacked-claim guard
  (`tests/test_launch_frontend_claims.py`) stays in place, because the new
  licence does not restore an "open source" claim.
- `website/` stays out of the repository. The marketing site ships separately
  via its own hosting, so a clone of this repository is the agent/API/
  warehouse/exports/app-UI source and the docs — not the marketing site.
  `docs/FRONTENDS.md` still documents both frontends.

## What this does not decide

The relicense is not retroactive. Earlier binaries were distributed under an
AGPL-intent distribution; what those recipients were promised does not change
today, and their source-offer obligations need legal confirmation before the
installer is redistributed further.

## Consequences

- The source is visible and verifiable at the repository URL; the
  "source code is not published yet" copy is retired in favour of pointing at
  the repo and the licence.
- The tests are updated: the licence guard asserts PolyForm, and site tests
  skip when `website/` is absent from a clone.
- Any marketing copy that said the source is not published yet must be
  updated to point at the repository and the licence; the "open source"
  wording stays banned.
- The AGPL-era binary-recipient question remains a legal follow-up (see
  "What this does not decide").
- Commercial reuse of the source is handled by request to the owner.
