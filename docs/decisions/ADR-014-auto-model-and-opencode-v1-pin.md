# ADR-014: the opencode model is chosen automatically, and the CLI stays on v1

**Status:** accepted
**Date:** 2026-10-22
**Relates to:** ADR-012 (pinned, hash-verified downloads), ADR-010 (local-first v1)

## Context

The Data Agent AI can reach an LLM through the anonymous **opencode free
tier**: no API key, no signup. Two things about that tier are awkward.

**The model set rotates.** The free tier does not promise a fixed model
list — it publishes one (currently `big-pickle`, `beam-free`,
`flint-40b-free`, `minimax-m3.1-flash-free`, `nemotron-3.5-lightning-free`,
`nemotron-3-ultra-free`, `nemotron-3.5-super-vl-free`,
`mimo-v2.6-flash-free`, `deepseek-v4.1-flash-free`, `glm-5.3-flash-free`)
and changes it without notice. A hard-coded id is a promise the app cannot
keep: the id that was fastest last month may be gone, or may have become
the slowest.

**The default was a reasoning model.** `big-pickle` is a reasoning model:
it runs a long silent thinking phase before it answers. Measured through
the app's own provider, one analytics answer took **607 s**. The same
question on `nemotron-3.5-lightning-free` took **27 s** — a ~22× win, for
the same free tier and the same code path.

There was a second, separate problem. The project pinned the opencode CLI
to a **v1** release, but the host's npm prefix had been taken over by
`@opencode/cli` (v2), which **replaced the `opencode` shim**. So `opencode`
on `PATH` was v2, the server answering on the loopback port was v2, and the
CLI the app actually shipped was v1 — three different opencodes in one
machine, and nothing said so.

## Decision

> **Revision note (2026-10-23):** decision 3 below was reversed by
> ADR-015. The provider now speaks only the v1 session API and rejects any
> other server; the text below records the original decision. Decision 1's
> proposed move to `v1.18.30` was **not adopted** — the shipped pin remains
> the committed **`v1.19.3`** with its four SHA256 files.

**1. The CLI stays on a v1 release, pinned and hash-verified.**

The pin stays on **`v1.19.3`** in all four places (the bootstrap, the
Windows build, the Linux build, the Linux installer), with committed SHA256
pins for the Windows executable and all three Linux tarballs. The npm
package ships the **same binary** as the GitHub release, so the pin can be
verified without the CDN — a fact worth recording, because it is what caught
the shim hijack.

**2. The model is chosen automatically.**

`model: "auto"` is the default. Before each call the provider asks the
opencode server which models it actually offers and picks the fastest
**free** one it knows about, falling back to the server's own default. The
preference list is ordered by measured latency, and a model the server does
not offer is never chosen — the server's list is the truth, the list is
only a preference.

**3. A server that is not the pinned one is reported, not refused.**
*(Superseded by ADR-015 on 2026-10-23: such a server is now refused.)*

The provider speaks **both** session APIs: the modern v2 one
(`POST /api/session`, `POST /api/session/{id}/generate`) and the legacy v1
one (`POST /session`, `POST /session/{id}/message`), and it detects which
one a server speaks before using it. Refusing a v2 server would break a
configuration that works. So the app reports it — the server kind and its
version now appear in `server_status`, and a stray server produces a
warning naming the CLI the app ships — instead of using it silently.

**4. The server runs in a directory with no project configuration.**

opencode discovers its MCP servers from the project configuration, which it
finds in its **working directory**. Started in the app's own directory, the
server inherited the project's MCP servers — among them a `duckdb` server
(direct access to the warehouse, around the app's row-level security and
its one-writer discipline) and a `filesystem` server scoped to `config/`
and `data/`, which is where `data/.jwt_secret` and `data/.grant_key` live.
The app's LLM could therefore have read the app's signing keys and written
to the warehouse.

The server is now started in a dedicated, empty directory. It still finds
the user's *global* opencode configuration (their own choice) and still
serves the free tier — but the project's tools never reach the model.

## Consequences

* **The free tier's rotation stops being the app's problem.** The default
  follows the tier; a model that disappears stops costing anything.
* **Latency is bounded by the model, not by a reasoning phase.** The
  22× difference between the old default and the fastest free model is now
  the default behaviour.
* **"Auto" is only as good as the preference list.** It is a measured
  snapshot; a model that regresses stays in the list until it is measured
  again. The list is therefore short and ordered, and every entry has to
  have answered a prompt.
* **The v1 path is slower than the v2 path for the same model** (27 s vs
  3.7 s for the winner). That is the price of pinning v1, and it is
  visible rather than hidden.
* **An empty completion is now an error.** The v2 generate route answers
  `{"data": {"text": ""}}` when the model has nothing to say. The provider
  now checks the reasoning channel and, failing that, raises — so the
  caller falls back to the deterministic answer instead of showing the user
  an empty one.
* **The LLM no longer holds the app's tools.** Started in a directory with
  no project configuration, the server exposes no DuckDB server and no
  filesystem server, so the model cannot read `data/.jwt_secret`,
  `data/.grant_key`, `config/users.json` or `config/db_connections.json`,
  and cannot touch the warehouse behind the app's row-level security.
* **The probe must use the server's credentials.** An opencode server can
  require HTTP Basic auth — the app generates a password for the server it
  starts — so the availability probe sends the same headers the provider
  uses. Without them a healthy server answered 401 and every answer was
  labelled "rule-based fallback".
* **The pin is the thing that ships.** The npm shim is no longer load
  bearing: the app resolves its own binary (bundled → app dir → host →
  runtime copy) and the runtime copy is named after the version it holds.

## Alternatives rejected

* **Pin one concrete fast model** (`nemotron-3.5-lightning-free`). Cheaper
  to reason about, but it is exactly the promise the free tier breaks.
* **Keep `big-pickle`.** It is the most capable model in the set; it is
  also 22× slower on the path the app ships.
* **Refuse any server that is not the pinned v1.** Rejected at the time
  because it would break a working configuration; adopted later in ADR-015
  once the dual-dialect path was removed.
