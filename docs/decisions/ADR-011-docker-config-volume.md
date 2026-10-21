# ADR-011 — the Docker config mount is a writable volume, not a read-only bind

- **Status:** Accepted
- **Date:** 2026-10-21
- **Relates to:** ADR-010 (v1 is local-first), `docs/LAUNCH-TECHNICAL-REVIEW.md` §3 (item 15)

---

## Context

`docker-compose.yml` mounted the config directory read-only:

```yaml
- ./config:/app/config:ro
```

Three routes persist runtime state under `config/`:

- `POST /llm/providers/update` → `config/llm_runtime.json`
- `POST /pipeline/save` → `config/pipelines/…`
- `POST /pipeline/schedule` → `config/schedules/…`

With a read-only mount those writes raised a permission error, so **the Settings
page could not change the LLM provider** — the single most important setting in
the product. The failure was silent: the route returned `{"success": false}` and
the operator had no way to tell "you are not allowed" from "the disk is
read-only".

A read-only mount also has two further costs: the container's writes would land
in the developer's checkout (so `config/users.json` and the revoked-JWT list
would be handed to the container), and a container restart would lose the
runtime state.

## Decision

`/app/config` is a **named writable volume** (`config_runtime`), with the three
seed files bind-mounted read-only *over* it:

```yaml
- config_runtime:/app/config
- ./config/settings.yaml:/app/config/settings.yaml:ro
- ./config/model_config.yaml:/app/config/model_config.yaml:ro
- ./config/db_config.yaml:/app/config/db_config.yaml:ro
```

So a fresh `docker compose up` has both: the seeds are present and immutable,
and the runtime state is writable and survives a restart.

## Consequences

- The Settings page, the pipeline editor and the scheduler can all persist.
- The host checkout is no longer written by the container, and
  `config/users.json` is no longer handed to it.
- The revocation list (`config/revoked_jtis.json`) and the OAuth token are
  excluded by `.dockerignore` and are no longer part of the image.
- **Accepted risk:** if Docker mounts the volume *over* the seed binds, the
  container would use the image's own seed yaml. The seeds are still present
  in the image, so the app starts correctly; only a host-side edit to
  `settings.yaml` would be ignored. The fallback is to drop `:ro` from the
  `./config` bind.
- Not verified at runtime here: the Docker daemon was not running on the build
  machine. `docker compose config` validates (exit 0).
