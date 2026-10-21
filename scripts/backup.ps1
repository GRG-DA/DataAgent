# DataAgent backup — copies warehouse + credential state to backups/<stamp>/.
# Stop writes first for a consistent DuckDB snapshot:
#   docker compose stop api   (or stop the bare-metal server)
# Usage: powershell -File scripts/backup.ps1
$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = "backups/$stamp"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item "data/curated/warehouse.duckdb" "$dest/" -ErrorAction SilentlyContinue
Copy-Item "data/curated/warehouse.duckdb.wal" "$dest/" -ErrorAction SilentlyContinue
# Identity store (password hashes, salts, MFA secrets) — ADR-009 D2.
# The -wal/-shm sidecars matter: the store runs in WAL mode.
Copy-Item "data/users.db" "$dest/" -ErrorAction SilentlyContinue
Copy-Item "data/users.db-wal" "$dest/" -ErrorAction SilentlyContinue
Copy-Item "data/users.db-shm" "$dest/" -ErrorAction SilentlyContinue
Copy-Item "config/users.json" "$dest/" -ErrorAction SilentlyContinue
Copy-Item "config/llm_runtime.json" "$dest/" -ErrorAction SilentlyContinue
Copy-Item "config/db_connections.json" "$dest/" -ErrorAction SilentlyContinue
Copy-Item ".env" "$dest/env.bak" -ErrorAction SilentlyContinue
Write-Host "backup written to $dest"
Write-Host "restore: stop the server, copy files back, restart (see docs/PRODUCTION.md)"
