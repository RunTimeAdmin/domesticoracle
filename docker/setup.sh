#!/usr/bin/env bash
# Domestic Oracle — first-run setup (Linux / macOS)
# Generates .env with a random ORA_OWNER_TOKEN if one doesn't exist yet.
# Run once before: docker compose up --build
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE="$ROOT/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    cp "$EXAMPLE" "$ENV_FILE"
    echo "Created .env from .env.example"
fi

if ! grep -qE 'ORA_OWNER_TOKEN=\S' "$ENV_FILE"; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    # In-place sed that works on both GNU and BSD (macOS) sed
    sed -i.bak "s/ORA_OWNER_TOKEN=/ORA_OWNER_TOKEN=$TOKEN/" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
    echo ""
    echo "Generated ORA_OWNER_TOKEN: $TOKEN"
    echo "This token is your owner password. Keep .env private."
else
    echo "ORA_OWNER_TOKEN already set — skipping generation."
fi

echo ""
echo "Next steps:"
echo "  1. Edit .env and set ANTHROPIC_API_KEY=sk-ant-..."
echo "  2. (Optional) Set ORA_HA_URL / ORA_HA_TOKEN for Home Assistant"
echo "  3. docker compose up --build"
echo ""
echo "On first start the backend generates its Ed25519 signing key at /data/oracle_keys/."
echo "That volume persists across rebuilds — do not delete it."
