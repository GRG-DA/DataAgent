# ADR-021 — Keep the source private until it is published

**Date:** 2026-10-26
**Status:** Accepted for the launch
**Relates to:** ADR-010 (v1 is local-first), `docs/marketing/claim-audit.md`,
`docs/marketing/preflight-checklist.md`, `website/`, `tests/test_launch_frontend_claims.py`

## Context

The marketing site and the social kit claimed that DataAgent is open source
under the GNU AGPL v3. The public release repository
(`java22113344/grg-dataagent`) publishes the Windows installer and release
notes, but not the source. The repository does contain a `LICENSE` file, and a
test enforced the AGPL naming on the site.

That left two problems:

1. **The claim was not backed by a public source repository.** A visitor could
   not read, fork or verify the code, which is exactly what an open-source
   claim promises.
2. **The claim was being repeated in launch copy.** The first 30-day plan
   needed one honest answer before the first short was posted.

## Decision

For the launch, the source stays unpublished. The public marketing surface
must not claim that DataAgent is open source or name the AGPL as the licence of
the distributed app.

- Remove the open-source/AGPL claim from every marketing page, including the
  footer, FAQ, about page, homepage, changelog, docs, contact page, comparison
  page, use cases, blog and blog post.
- Replace the AGPL-naming test with a guard that fails if any marketing page
  makes an unbacked open-source or AGPL claim. The only allowed “open source”
  reference is the competitor category on `comparison.html`
  (Metabase/Superset).
- The site may say the source code is not published yet, and may describe the
  packaged app, the local data path and the network disclosure.
- Do not use “open source” in a caption, title, thumbnail or release README
  until a public source repository is live.
- The release README draft uses a neutral “source status” section, not an AGPL
  line.

## What this does not decide

This is a marketing-copy decision, not a relicense. The repository’s
`LICENSE` file remains in place. Because the app is distributed as a binary,
the AGPL’s source-offer obligation still needs a legal review. Publishing the
source under AGPL, or changing the licence, is a separate decision.

Before distributing the app at scale, get legal advice on one of these paths:

1. publish the source under the existing AGPL and restore the claim; or
2. change the licence and publish the corresponding terms; or
3. keep the source private under a licence that matches the distribution.

## Consequences

- The site is honest about the current state: the source is not published yet.
- The launch does not depend on making the source public.
- The tests now protect the absence of an unbacked claim instead of protecting
  the claim itself.
- The open-source question remains a known follow-up, not a silent
  contradiction.
