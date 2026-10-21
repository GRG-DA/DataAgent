# Enable public HTTPS — run on the host (needs Docker + DNS pointing at this host).
# Usage: powershell -File scripts/enable_tls.ps1 -Domain analytics.example.com -Email you@example.com
param([Parameter(Mandatory = $true)][string]$Domain, [Parameter(Mandatory = $true)][string]$Email)
$ErrorActionPreference = "Stop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host "Docker is not installed."; exit 1 }
# 1. Issue cert via webroot against the running nginx (port 80 serves challenges).
docker run --rm -v "${PWD}/web:/var/www/html:ro" -v "certs:/etc/letsencrypt" certbot/certbot `
  certonly --webroot -w /var/www/html -d $Domain --email $Email --agree-tos --non-interactive
# 2. Activate the 443 block in nginx.conf for this domain.
$lines = Get-Content "nginx.conf"
$inBlock = $false; $done = $false; $out = @()
foreach ($line in $lines) {
  if (-not $done -and $line -match "^# server \{$") { $inBlock = $true; $out += "server {"; continue }
  if ($inBlock) {
    if ($line -match "^# \}$") { $out += "}"; $inBlock = $false; $done = $true; continue }
    $out += ($line -replace "^# ", "")
    continue
  }
  $out += $line
}
if (-not $done) { Write-Host "No 443 template block found in nginx.conf"; exit 1 }
$out = $out -replace "analytics\.example\.com", $Domain
Set-Content "nginx.conf" ($out -join "`r`n")
Write-Host "nginx.conf updated for $Domain. Mount certs into the web container,"
Write-Host "add '- certs:/etc/letsencrypt:ro' under web.volumes in docker-compose.yml,"
Write-Host "add 'certs:' under top-level volumes, then: docker compose up -d --force-recreate web"
