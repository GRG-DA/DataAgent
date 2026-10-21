# Backup and restore (hosted deployment)

How a hosted DataAgent deployment is backed up, how it is verified, and how it is
put back. Written for the operator of the service, not for the end user.

- **Tool:** `scripts/hosted_backup.py` (and `scripts/hosted_restore.py`, a thin
  wrapper around its `restore` command).
- **Scope:** the DuckDB warehouse, the SQLite identity store, the SQLite audit
  sink, the raw uploads, the per-tenant warehouse files and the non-secret
  configuration.
- **Not in scope:** the billing service's own database, and the external billing
  system (those are backed up by whoever runs them).

---

## 1. The one thing to understand first

**DuckDB allows one writer per database file.** While the application is running
it holds `data/curated/warehouse.duckdb` open, and *no other process* — not even
a read-only one — may open that file. On Windows the error is
`IO Error: File is already open in <pid>`; on Linux it is a conflicting lock.

The backup tool therefore does **not** copy the live `.duckdb` file. It opens the
warehouse **read-only** and runs `EXPORT DATABASE`, which produces a consistent
snapshot (one CSV per table, plus `schema.sql` and `load.sql`) without competing
with the application's writer.

If the file is locked, the tool **refuses** with an explicit message instead of
copying a file that is being written to:

```
ERROR: the warehouse is locked by another process -- DataAgent is most likely
running. DuckDB allows a single writer per file, so the snapshot has to wait.
Stop the app (or take a volume snapshot) and run the backup again.
```

### So when do I back up?

| Situation | How to back up |
|---|---|
| The app is running (normal hosted operation) | Take a **volume snapshot** of the data disk at the infrastructure level, or run the tool against a snapshot volume. The DuckDB file is single-writer, so no in-process copy can be taken while the app is up. |
| The app is stopped (maintenance window) | Run `hosted_backup.py backup` directly. This is the recommended path for a nightly job that briefly stops the writer. |
| Per-tenant database mode is on | Back up `data/tenants/` **as well as** `data/curated/`. The tool does both automatically. |

> **Note on `EXPORT DATABASE`.** It does not carry secondary indexes. The
> application recreates them on boot (`CREATE INDEX IF NOT EXISTS`), so this is
> not data loss — but a restore does rebuild the indexes, which takes time on a
> large warehouse.

---

## 2. Taking a backup

```bash
# The default root is the current directory.
python scripts/hosted_backup.py backup --out /var/backups/dataagent

# With a label, so the directory is easy to find later.
python scripts/hosted_backup.py backup --out /var/backups/dataagent --label nightly

# From somewhere else, against an explicit installation root.
python scripts/hosted_backup.py backup --root /opt/dataagent --out /var/backups/dataagent
```

The output looks like this:

```
Backup written to /var/backups/dataagent/2026-10-20T02-00-00Z-nightly
  files      : 1284
  secrets    : excluded
  excluded   :
               - config/llm_runtime.json
               - config/revoked_jtis.json
               - config/users.json
               - data/.grant_key
               - data/.jwt_secret
```

### What is in the directory

```
<timestamp>[-<label>]/
  manifest.json              the manifest (see below)
  warehouse/export/          the DuckDB snapshot: schema.sql, load.sql, *.csv
  data/users.db              the SQLite identity store
  data/audit.db              the SQLite audit sink, when it exists
  data/raw/<tenant>/...      the raw uploads
  data/tenants/<tenant>/...  the per-tenant warehouse files, in that mode
  config/*.yaml              the non-secret configuration
```

### The manifest

`manifest.json` records the tool version, the UTC creation time, whether
secrets were included, **which secrets were excluded**, the method used for the
warehouse snapshot, and one entry per file with its size and its SHA-256.

```json
{
  "version": 1,
  "tool": "scripts/hosted_backup.py",
  "created_at": "2026-10-20T02:00:03Z",
  "label": "nightly",
  "backup_dir": "/var/backups/dataagent/2026-10-20T02-00-00Z-nightly",
  "includes_secrets": false,
  "excluded_secrets": ["config/users.json", "data/.grant_key", "data/.jwt_secret"],
  "warehouse": {"method": "export_database", "source": "warehouse.duckdb"},
  "files": [{"path": "warehouse/export/schema.sql", "size": 4812, "sha256": "..."}]
}
```

---

## 3. Secrets are excluded by default

The following are **never** copied unless you pass `--include-secrets`:

| Path | What it is |
|---|---|
| `data/.jwt_secret` | the session-signing key |
| `data/.grant_key` | the entitlement-signing key |
| `data/.grant_migrated` | the entitlement-migration marker |
| `config/users.json` | the legacy identity store (contains password hashes) |
| `config/db_connections.json` | external database credentials |
| `config/google_login_pending.json` | a pending Google login transaction |
| `config/llm_runtime.json` | the runtime model configuration (may hold API keys) |
| `config/revoked_jtis.json` | the revoked-token list |
| `.env` | the environment file |

The tool prints the excluded list on every run, so an operator can see at a
glance what is *not* in the archive.

`--include-secrets` copies them and prints a warning:

```
WARNING: --include-secrets copies credential material (JWT secret, grant key,
credential files) into the backup. The archive is now as sensitive as the live
installation -- encrypt it and restrict it.
```

**Use `--include-secrets` only** when the destination is encrypted at rest, the
archive is access-controlled to the same people who can read the live secrets,
and the archive's lifetime is no longer than the secrets' own lifetime. A
backup of the signing keys is a backup of the ability to mint sessions.

> In a hosted deployment the entitlement key usually does **not** belong on the
> data host at all — the billing server should be the authority for paid plans
> (`DATAAGENT_GRANT_KEY`).

---

## 4. Verifying a backup

```bash
python scripts/hosted_backup.py verify --from /var/backups/dataagent/2026-10-20T02-00-00Z-nightly
```

The verifier re-hashes every file in the manifest and compares it with the
recorded hash and size.

```json
{
  "backup_dir": "/var/backups/dataagent/2026-10-20T02-00-00Z-nightly",
  "ok": true,
  "checked": 1284,
  "missing": [],
  "mismatched": []
}
```

Exit code `0` when the backup is intact, `1` when it is not. A restore refuses
to run against a backup that does not verify, so a silent corruption cannot
become a silent outage.

---

## 5. Restoring

**A restore is destructive.** It replaces the live warehouse and the identity
store. It refuses to run without an explicit confirmation, in the same spirit as
the `DELETE /tables` endpoint:

```bash
# Refused:
$ python scripts/hosted_restore.py --from /var/backups/dataagent/2026-10-20T02-00-00Z-nightly
REFUSED: restoring replaces the live warehouse and the identity store. Re-run
with --confirm once the app is stopped.

# Accepted:
$ python scripts/hosted_restore.py --from /var/backups/dataagent/2026-10-20T02-00-00Z-nightly --confirm
Restore complete: {'warehouse': True, 'data/users.db': True, 'data/raw': True}
  previous warehouse kept as warehouse.duckdb.pre-restore-2026-10-20T04-12-00Z
```

What a restore does:

1. Verifies the backup against its manifest. A failed verification aborts.
2. Moves the live `warehouse.duckdb` (and its write-ahead log) aside to
   `warehouse.duckdb.pre-restore-<timestamp>`.
3. Rebuilds the warehouse with `IMPORT DATABASE` from the exported snapshot.
4. Moves the live `data/users.db` and `data/audit.db` aside and copies the
   backed-up versions back.
5. Copies the raw uploads back.
6. Restores secret files **only** when the backup contains them *and*
   `--include-secrets` is given.

### Restore drill

Run this on a schedule (monthly is a reasonable default) and keep the result.

1. Stop the application.
2. Restore the most recent backup into a **scratch** installation root:
   ```bash
   python scripts/hosted_backup.py restore \
     --from /var/backups/dataagent/2026-10-20T02-00-00Z-nightly \
     --root /tmp/restore-drill --confirm
   ```
3. Start the application against the scratch root and check:
   ```bash
   curl -s http://127.0.0.1:8002/health/system | python -m json.tool
   ```
   The `warehouse` and `identity_store` components must both be `ok: true`.
4. Spot-check the data: sign in, list the tables, run one query, open one
   dashboard.
5. Delete the scratch root.

A backup that has never been restored is a hypothesis, not a backup. Record the
date of the last successful drill next to the retention policy.

---

## 6. Retention

| Copy | Keipped | Where |
|---|---|---|
| Nightly backup | 35 days, rolling | the backup volume |
| Monthly archive | 12 months | off-site, encrypted |
| On-volume copy | 24 hours | the backup volume |

The nightly backup is what the restore drill exercises. The monthly archive
exists for the "we need the data as it was in March" case, so it must be
restorable with the same tool and the same procedure.

Adjust the numbers to your own obligations: the
[Privacy Policy](privacy.html) states the customer-facing retention window,
and this page states the operational one. If they disagree, the Privacy Policy
is the one you are bound by.

---

## 7. Running it on a schedule

### Linux (systemd timer)

```ini
# /etc/systemd/system/dataagent-backup.service
[Unit]
Description=DataAgent hosted backup
After=dataagent.service

[Service]
Type=oneshot
ExecStartPre=/bin/systemctl stop dataagent.service
ExecStart=/usr/bin/python3 /opt/dataagent/scripts/hosted_backup.py backup \
    --root /opt/dataagent --out /var/backups/dataagent --label nightly
ExecStartPost=/bin/systemctl start dataagent.service
```

```ini
# /etc/systemd/system/dataagent-backup.timer
[Unit]
Description=Nightly DataAgent backup

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### Windows (Task Scheduler)

Run a small wrapper that stops the service, runs the backup and starts the
service again:

```powershell
# scripts/backup_nightly.ps1
Stop-Service  DataAgent
python scripts\hosted_backup.py backup --root . --out D:\backups\dataagent --label nightly
Start-Service DataAgent
```

### With the app left running

Take an infrastructure-level snapshot of the data volume instead. The snapshot
must be a **crash-consistent** snapshot of the whole volume, and the DuckDB
file and its write-ahead log must be captured in the same snapshot. Snapshot
the volume, then run the restore drill against the snapshot to prove that the
result is usable.

---

## 8. What a backup does not cover

- **The billing service's own database.** Back it up with the tooling that
  belongs to that service.
- **The grant-signing key**, unless you explicitly asked for it. On a hosted
  deployment the billing server should hold it.
- **Anything outside the data directory.** If you run the app in a container,
  the volume that holds `data/` is what has to be snapshotted.
- **The model provider's data.** We do not control it; the Privacy Policy
  states what is sent.

---

## 9. Verifying a deployment

The hosted service exposes the endpoints an operator needs to know that it is
alive and that its dependencies are healthy:

| Endpoint | What it answers | Touches dependencies? |
|---|---|---|
| `GET /health/live` | is the process up? | no |
| `GET /health/ready` | is the warehouse usable? | the warehouse only |
| `GET /health/system` | is every dependency healthy? | all of them, individually |
| `GET /metrics` | the Prometheus exposition | no |

```bash
curl -s http://127.0.0.1:8002/health/system | python -m json.tool
```

```json
{
  "status": "degraded",
  "components": {
    "api":            {"ok": true,  "critical": true,  "reason": "process is serving requests"},
    "warehouse":      {"ok": true,  "critical": true,  "reason": "warehouse is readable and writable"},
    "identity_store": {"ok": true,  "critical": true,  "reason": "identity store is readable"},
    "audit_sink":     {"ok": true,  "critical": false, "reason": "file audit sink is configured"},
    "llm_provider":   {"ok": false, "critical": false, "reason": "llm provider is unreachable (rule-based fallback)"},
    "billing":        {"ok": true,  "critical": false, "reason": "billing service is reachable"},
    "opencode_server":{"ok": true,  "critical": false, "reason": "opencode server is not configured"}
  }
}
```

`healthy` means every component is fine, `degraded` means an optional
dependency is down (the service still works -- an unreachable model provider
falls back to rule-based answers) and `unhealthy` means the warehouse or the
identity store is broken (HTTP 503, and the instance is removed from the load
balancer's rotation).

---

## 10. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `the warehouse is locked by another process` | The app holds the file | Stop the app, or snapshot the volume instead |
| `the backup failed verification; refusing to restore` | A file was truncated or edited | Restore from another copy; investigate the storage layer |
| `identity database is missing` in the health check | `data/users.db` was never created or was deleted | Check the `DATAAGENT_USER_DB` environment variable |
| `the identity database is in use by another process` on restore | The app is running | Stop the app before restoring |
| Restore succeeds but the app shows no tables | The restore targeted the wrong `--root` | Restore again with the correct root |

---

## 11. Related documents

- [`docs/SAAS-OPERATIONS.md`](SAAS-OPERATIONS.md) — running the hosted service.
- [`docs/OPERATIONS.md`](OPERATIONS.md) — the general runbook.
- [`docs/decisions/ADR-009-hosted-multi-tenant-saas.md`](decisions/ADR-009-hosted-multi-tenant-saas.md)
  — why the data plane moved to the server.
- [`web/legal/security.html`](../web/legal/security.html) — the customer-facing
  security summary.
