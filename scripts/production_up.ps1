# Production bring-up — run on the host (needs Docker). One command:
#   powershell -File scripts/production_up.ps1
$ErrorActionPreference = "Stop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Host "Docker is not installed. Install Docker Desktop, then re-run."; exit 1
}
if (-not (Test-Path ".env")) {
  Write-Host ".env missing. Run: cp .env.example .env  (then set JWT_SECRET)"; exit 1
}
$jwt = (Select-String -Path ".env" -Pattern "^JWT_SECRET=(.+)" | ForEach-Object { $_.Matches[0].Groups[1].Value }).Trim()
if (-not $jwt -or $jwt.Length -lt 16 -or $jwt -eq "change-me-in-production") {
  Write-Host "JWT_SECRET in .env is missing/placeholder/short. Generate:"; Write-Host '  python -c "import secrets;print(secrets.token_hex(32))"'; exit 1
}
docker compose config | Out-Null
docker compose up --build -d
for ($i = 0; $i -lt 30; $i++) {
  try { Invoke-RestMethod -Uri http://127.0.0.1:8002/health -TimeoutSec 5 | Out-Null; Write-Host "API healthy"; break }
  catch { Start-Sleep -Seconds 5 }
  if ($i -eq 29) { Write-Host "API did not become healthy in 150s. Check: docker compose logs api"; exit 1 }
}
Write-Host "up. Next for public HTTPS: powershell -File scripts/enable_tls.ps1 -Domain <your-domain> -Email <you@example.com>"
