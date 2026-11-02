<#
  Windows 11 PowerShell helper (Docker Desktop with the WSL2 backend).
  Usage:  .\scripts\dev.ps1 up | down | logs | ps | seed | superadmin | test | lint | secret | tracker-test | backup | monitoring
#>
param([Parameter(Mandatory = $true)][ValidateSet("up","down","logs","ps","test","lint","secret","tracker-test","seed","superadmin","backup","monitoring")][string]$Command)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

switch ($Command) {
  "up" {
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example - edit the passwords first." -ForegroundColor Yellow; exit 1 }
    docker compose up -d --build
    docker compose ps
  }
  "down"   { docker compose down }
  "seed"   { docker compose exec backend python manage.py seed_demo }
  "superadmin" {
    $email = Read-Host "Super Administrator email"
    docker compose exec backend python manage.py create_superadmin $email
  }
  "logs"   { docker compose logs -f --tail=100 }
  "ps"     { docker compose ps }
  "test" {
    Push-Location gateway;  python -m pytest -q; Pop-Location
    Push-Location backend;  $env:TEST_SQLITE = "1"; python -m pytest -q; Remove-Item Env:TEST_SQLITE; Pop-Location
    Push-Location frontend; npm test; Pop-Location
  }
  "lint" {
    Push-Location gateway;  ruff check .; mypy; Pop-Location
    Push-Location backend;  ruff check .; Pop-Location
    Push-Location frontend; npm run lint; npm run typecheck; Pop-Location
  }
  "backup" {
    # Nightly-style dump inside the postgres container, copied to .\backups on the host.
    docker compose exec -T postgres bash -c "mkdir -p /tmp/backups && pg_dump -U `$POSTGRES_USER -d `$POSTGRES_DB -Fc -f /tmp/backups/gps.dump"
    New-Item -ItemType Directory -Force backups | Out-Null
    docker compose cp postgres:/tmp/backups/gps.dump "backups/gps_$(Get-Date -Format yyyyMMddTHHmmss).dump"
  }
  "monitoring" { docker compose --profile monitoring up -d prometheus grafana }
  "secret" {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToBase64String($bytes).Replace("+","-").Replace("/","_").TrimEnd("=")
  }
  "tracker-test" {
    # Sends a Teltonika IMEI handshake to the local gateway; expects reply 01 for a registered IMEI.
    $client = New-Object System.Net.Sockets.TcpClient("127.0.0.1", 5027)
    $stream = $client.GetStream()
    $hex = "000F333536333037303432343431303133"
    $bytes = [byte[]] -split ($hex -replace '..', '0x$& ')
    $stream.Write($bytes, 0, $bytes.Length)
    $reply = $stream.ReadByte()
    Write-Host ("Gateway replied: {0:X2} ({1})" -f $reply, $(if ($reply -eq 1) { "accepted" } else { "rejected" }))
    $client.Close()
  }
}
