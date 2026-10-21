# Supervised bare-metal server - restarts `python run.py web` on unexpected exit.
# Usage: powershell -File scripts/serve.ps1   (stop with Ctrl+C)
$ErrorActionPreference = "Continue"
$backoff = 5
while ($true) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$ts] starting: python run.py web"
  python run.py web
  $code = $LASTEXITCODE
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$ts] exited with code $code - restarting in ${backoff}s (Ctrl+C to stop)"
  Start-Sleep -Seconds $backoff
  if ($backoff -lt 60) { $backoff = [Math]::Min(60, $backoff * 2) }
}
