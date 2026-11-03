# ADR-015: remove opencode v2 session-API support

- **Status:** Accepted
- **Date:** 2026-10-23
- **Relates to:** ADR-014 (auto model and the v1 pin), ADR-012 (pinned downloads)

## Context

ADR-014 kept two server dialects side by side: a v2 path
(`POST /api/session`, `POST /api/session/{id}/generate`) tried first, with the
v1 session API (`POST /session`, `POST /session/{id}/message`) as a fallback.
In practice the project ships and pins a v1 CLI, so the v2 path was a second
code path with no shipped user. It also made a stray v2 server on the loopback
port silently acceptable: the app would answer through an API the project does
not test or pin.

The v1.18.35 server confirms the contract this build needs:

- `GET /config/providers` lists the provider models and the default.
- `GET /global/health` returns `{"healthy": true, "version": "1.18.35"}`.
- `POST /session` / `POST /session/{id}/message` drive a turn.
- `POST /session/{id}/abort` stops it.

## Decision

**The provider speaks only the v1 session API.**

1. `agent/llm_client.py` no longer detects or calls v2 routes. The abort
   route is a single constant, and model discovery reads
   `GET /config/providers`.
2. `agent/opencode_bootstrap.py` probes only the v1 `/config` route. A server
   that answers with the v2 SPA shell (or any unrecognised body) is refused
   with an actionable error instead of being used.
3. The v2-only tests and helper methods were removed; the bootstrap tests now
   assert that a foreign server is rejected.
4. The bundled CLI pin is unchanged at **v1.19.3**, with its committed
   SHA256 pins for the Windows executable and all three Linux tarballs.

## Consequences

- **A v2 server cannot be used silently.** The app returns a clear error
  naming the port and telling the operator to stop the server or change
  `opencode_server_port`.
- **One contract to test.** Model discovery, chat, vision parts and abort are
  all exercised against the v1 routes only.
- **Less code and one less failure mode.** The dialect probe, the v2 envelope
  handling, the v2 prompt/generate split and the v2 empty-completion workaround
  are gone.
- **A future v2-only server is unsupported** until a new ADR reintroduces it
  deliberately, with tests and a pin.

## Alternatives rejected

- **Keep both dialects.** No shipped configuration needed v2, and the extra
  path made a foreign server look healthy.
- **Detect v2 and warn.** A warning still lets the app answer through an
  untested API; refusing is the safer default.
