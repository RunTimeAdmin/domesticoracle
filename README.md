# Domestic Oracle

**A governed AI operator for your home.**

Domestic Oracle is a self-hosted personal AI that can remember, search the web, read files, control your smart home, and take real-world actions — but every consequential action is constrained by a policy engine, an approval queue, and a tamper-evident cryptographic ledger that you control.

> **An AI that acts. With your permission.**

---

## What makes it different

Most AI assistants answer questions. Domestic Oracle *acts* — but only within rules you define. The Trust Center gives you:

- **Policy engine** — "deny purchases over $50", "block device control between midnight and 6am"
- **Approval queue** — hold any action for your explicit sign-off before it executes
- **Tamper-evident ledger** — every action is hash-chained and Ed25519-signed; integrity is verifiable
- **Agent identity** — revoke the AI's access instantly without a restart
- **Persistent memory** — remembers your preferences and context across every conversation

---

## Architecture

```
Browser (Next.js frontend)
        │
        ▼
FastAPI gateway  ──────────────────────────────────────────────────────┐
        │                                                               │
        ├── POST /chat  →  Oracle Agent (LangGraph + Claude Sonnet 4)  │
        │                         │                                     │
        │              ┌──────────┴──────────┐                         │
        │         SAFE tools           GUARDED tools                   │
        │       (web, files)    (HA control, purchases, messages)      │
        │                               │                              │
        │                    consent.request_action()                  │
        │                    ┌──────────┼──────────┐                   │
        │                 policy      ledger    approval               │
        │                 engine      append     queue                 │
        │                                                               │
        └── Trust Center API (ledger, policies, agents, approvals) ────┘
```

### Stack

| Layer | Technology |
|-------|-----------|
| Agent runtime | LangGraph + [DeerFlow](https://github.com/bytedance/deer-flow) harness |
| LLM | Claude Sonnet 4 via Anthropic API |
| Backend | FastAPI + Python 3.12 |
| Frontend | Next.js 14 + TypeScript + Tailwind CSS |
| Storage | SQLite (ledger, policies, agents, approvals, nonces) |
| Smart home | Home Assistant REST API (optional) |
| Crypto | Ed25519 via Python `cryptography` library |

---

## Prerequisites

- Python 3.12+
- Node.js 18+
- [Anthropic API key](https://console.anthropic.com/)
- [DeerFlow harness](https://github.com/bytedance/deer-flow) cloned locally
- Home Assistant (optional — a mock home is used if not configured)

---

## Quick start

### 1. Clone

```bash
git clone https://github.com/RunTimeAdmin/domesticoracle
cd domesticoracle
```

### 2. Install the DeerFlow harness

DeerFlow is the underlying agent runtime. Install its harness package from your local clone:

```bash
pip install -e "/path/to/deer-flow/backend/packages/harness"
```

### 3. Backend

```bash
cd backend
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY at minimum
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

On first run the backend prints the owner token:

```
Owner token: 0550d752...
```

Copy it — you need it for the frontend and for any owner-gated API calls.

### 4. Frontend

```bash
cd frontend
cp .env.local.example .env.local
# Set NEXT_PUBLIC_ORA_OWNER_TOKEN to the token printed above
npm install
npm run dev   # http://localhost:3100
```

> **Security note — localhost only.** `NEXT_PUBLIC_ORA_OWNER_TOKEN` is baked into the
> browser JavaScript bundle and is visible to anyone who can load the page. This is
> intentionally acceptable for localhost, single-owner use — the owner is the only person
> who can reach it. **Do not expose this frontend on a hosted site or shared network**
> without replacing auth. A hosted deployment needs a real login flow: server-side session,
> HttpOnly cookie, Secure + SameSite=Strict flags, no client-visible secret.

---

## Configuration

### Environment variables (`backend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `http://localhost:3100`) |
| `ORA_HA_URL` | No | Home Assistant URL (e.g. `http://homeassistant.local:8123`) |
| `ORA_HA_TOKEN` | No | Home Assistant long-lived access token |
| `ORA_OWNER_TOKEN` | No | Pin the owner token across restarts |

### Policy enforcement modes

Set via the Trust Center UI or `PUT /policy/mode`:

| Mode | Behaviour |
|------|-----------|
| `enforced` | Rules apply — actions held or denied as configured (default) |
| `audit_only` | All actions allowed but still logged |
| `permissive` | All actions allowed, minimal logging |

### Model (`backend/config.yaml`)

The agent model is configured in `config.yaml`. Default: `claude-sonnet-4-6`.

---

## Trust Center

The Trust Center (top-right drawer in the UI) is your control panel:

| Tab | What it shows |
|-----|--------------|
| **Pending** | Actions held for your approval — approve or deny each one |
| **Ledger** | Every action ever taken, with verdict, risk score, and timestamp |
| **Devices** | Smart home devices discovered via Home Assistant |
| **Policies** | Active rules — add natural-language rules or structured ones |
| **Agents** | Registered actors — revoke or restore access |

---

## API reference

All Trust Center endpoints require the `X-Ora-Owner` header. The Oracle agent cannot present this header — it can only act through the consent gate.

```
POST   /chat                        Stream a reply over SSE
GET    /health                      Liveness check

GET    /ledger                      Recent audit entries
GET    /ledger/verify               Chain integrity check
GET    /ledger/summary              Rolling summary by category

GET    /policies                    Active policies
POST   /policies                    Add a policy
DELETE /policies/{id}               Remove a policy
GET    /policy/mode                 Current enforcement mode
PUT    /policy/mode          [owner] Change enforcement mode

GET    /approvals                   Pending approvals
POST   /approvals/{id}/resolve      Approve or deny  [owner]

GET    /agents                      Registered actors
POST   /agents/register      [owner] Register an external agent
POST   /agents/{id}/revoke   [owner] Block an actor
POST   /agents/{id}/restore  [owner] Re-enable an actor

GET    /devices                     Smart home devices
POST   /devices/control      [owner] Control a device through the gate
```

---

## Roadmap

- [ ] Docker / one-command setup
- [ ] Telegram front-end
- [ ] Scheduled tasks ("every morning at 8am, summarise my emails")
- [ ] Email and calendar integration
- [ ] Multi-user households

---

## License

MIT — see [LICENSE](LICENSE).
