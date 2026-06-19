# Domestic Oracle — first-run setup (Windows)
# Generates .env with a random ORA_OWNER_TOKEN if one doesn't exist yet.
# Run once before: docker compose up --build

$root     = Split-Path $PSScriptRoot -Parent
$envFile  = Join-Path $root ".env"
$example  = Join-Path $root ".env.example"

if (-not (Test-Path $envFile)) {
    Copy-Item $example $envFile
    Write-Host "Created .env from .env.example"
}

$content = Get-Content $envFile -Raw

if ($content -notmatch 'ORA_OWNER_TOKEN=\S') {
    # Generate 32 bytes of cryptographic randomness as hex
    $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    $token = ($bytes | ForEach-Object { '{0:x2}' -f $_ }) -join ''
    $content = $content -replace 'ORA_OWNER_TOKEN=', "ORA_OWNER_TOKEN=$token"
    Set-Content $envFile $content -NoNewline
    Write-Host ""
    Write-Host "Generated ORA_OWNER_TOKEN: $token"
    Write-Host "This token is your owner password. Keep .env private."
} else {
    Write-Host "ORA_OWNER_TOKEN already set — skipping generation."
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env and set ANTHROPIC_API_KEY=sk-ant-..."
Write-Host "  2. (Optional) Set ORA_HA_URL / ORA_HA_TOKEN for Home Assistant"
Write-Host "  3. docker compose up --build"
Write-Host ""
Write-Host "On first start the backend generates its Ed25519 signing key at /data/oracle_keys/."
Write-Host "That volume persists across rebuilds — do not delete it."
