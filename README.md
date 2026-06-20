# Domestic Oracle

**Self-hosted AI home assistant with a built-in governance layer.**

Domestic Oracle is an open-source, privacy-first AI assistant you run on your own hardware. It can remember your preferences, search the web, read and write files, control your smart home via Home Assistant, and take real-world actions, but every consequential action passes through a policy engine, an approval queue, and a tamper-evident cryptographic audit ledger that you own and control.

> **An AI that acts. With your permission.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/tests-126%20passing-brightgreen)](#testing)

---

## Why Domestic Oracle

Most AI assistants are chat interfaces: they answer questions but cannot act on your behalf. Agentic AI assistants that *can* act raise a harder problem: **how do you stay in control?**

Domestic Oracle solves this with a mandatory consent layer between the AI and the real world:

- **Before any consequential action executes**, policy rules are evaluated
- **Anything the policy holds** goes into an approval queue; you approve or deny it
- **Every action, regardless of verdict**, is written to a hash-chained, Ed25519-signed audit ledger you can verify independently
- **The AI can never forge its own permission**; internal actors cannot present the owner header, and external agents must present a valid cryptographic signature

---

## Key features

| Feature | What it means |
|---------|--------------|
| **Policy engine** | Configurable rules: spend limits, time windows, action denials, recipient blocks |
| **Approval queue** | Any rule hit becomes a held action; you approve or deny it in the Trust Center UI |
| **Cryptographic audit ledger** | Ed25519-signed, SHA-256 hash-chained; tamper-evident and independently verifiable |
| **Agent identity and revocation** | Every actor has an ID and keypair; revoke access instantly, no restart needed |
| **Persistent memory** | Remembers your preferences, routines, and context across all conversations |
| **Home Assistant integration** | Control lights, locks, climate, and any HA entity through the consent gate |
| **MCP server** | Exposes the governance tools as a Model Context Protocol endpoint |
| **Key rotation** | Rotate the signing key without breaking historical chain verification |
| **Provenance scanning** | All external content is scanned for prompt-injection signals before the AI sees it |
| **Blast-radius circuit breaker** | Per-actor hourly and global daily action caps prevent runaway agents |
| **AtomicMail email integration** | Send, reply to, and read email via a governed `@atomicmail.ai` inbox; every outgoing message is held for approval |

---

## Architecture

```
Browser (Next.js 14 + TypeScript)
        │
        ▼
FastAPI gateway  (main.py)
        │
        ├── POST /chat  →  Oracle Agent (LangGraph + Claude Sonnet 4)
        │                         │
        │              ┌──────────┴──────────────┐
        │         SAFE tools               GUARDED tools
        │     web search, files,        HA device control,
        │     weather, discovery         purchases, messages
        │                                       │
        │                         consent.request_action()
        │                         ┌──────────┼──────────┐
        │                      policy      ledger    approval
        │                      engine      append     queue
        │                      evaluate    sign       hold/notify
        │
        └── Trust Center API
              /ledger  /policies  /agents  /approvals  /keys  /mcp
```

The consent gate is a single chokepoint: **every guarded action flows through `consent.request_action()`**, which evaluates policy, appends to the ledger, and either executes, holds, or blocks, in that order, atomically.

### Technology stack

| Layer | Technology |
|-------|-----------|
| Agent runtime | LangGraph + [DeerFlow](https://github.com/bytedance/deer-flow) harness |
| LLM | Claude Sonnet 4 (via Anthropic API) |
| Backend | FastAPI + Python 3.12 |
| Frontend | Next.js 14 + TypeScript + Tailwind CSS |
| Storage | SQLite (ledger, policies, agents, approvals, sessions, nonces) |
| Smart home | Home Assistant REST API (optional) |
| Cryptography | Ed25519 via Python `cryptography` library |
| MCP | Model Context Protocol SSE server |

---

## Who is this for

**Home users who want a capable AI assistant without giving up control.** If you have a Home Assistant setup, run a home server or NAS, and want an AI that can actually do things (control devices, search, remember, take action) but draws a hard line at acting without your knowledge, this is built for you.

**Developers building governed AI systems.** The consent gate, policy engine, and audit ledger are cleanly separated modules. Embed them in your own agentic application to get auditability and human-in-the-loop control without reimplementing the infrastructure.

**Researchers and practitioners in AI safety and AI governance.** Domestic Oracle is a working reference implementation of runtime governance for LLM agents: hard policy constraints, cryptographic accountability, and human override at every decision point.

---

## How it's different

| | Domestic Oracle | Typical AI assistant | Open-source agent framework |
|--|--|--|--|
| **Actions gated by policy** | Yes, hard | No | No |
| **Cryptographic audit trail** | Yes, hash-chained Ed25519-signed | No | No |
| **Human approval queue** | Yes, built-in UI | No | No |
| **Agent revocation** | Per-actor, instant, no restart | No | Varies |
| **Prompt injection defence** | Provenance scanning on all external content | No | No |
| **Self-hosted / private** | Your hardware, your data | No | Yes |
| **Home Assistant integration** | Yes | Some | No |

---

## Prerequisites

- Python 3.12+
- Node.js 18+
- [Anthropic API key](https://console.anthropic.com/)
- [DeerFlow harness](https://github.com/bytedance/deer-flow) cloned locally
- Home Assistant (optional; a mock home is used if not configured)

---

## Quick start

### 1. Clone

```bash
git clone https://github.com/RunTimeAdmin/domesticoracle
cd domesticoracle
```

### 2. Install the DeerFlow harness

DeerFlow provides the LangGraph agent runtime. Install its harness package from your local clone:

```bash
pip install -e "/path/to/deer-flow/backend/packages/harness"
```

### 3. Backend

```bash
cd backend
cp .env.example .env
# Edit .env -- set ANTHROPIC_API_KEY at minimum
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

On first run the backend prints your owner token:

```
Owner token: 0550d752...
```

Copy it; you need it for the frontend and for owner-gated API calls.

### 4. Frontend

```bash
cd frontend
cp .env.local.example .env.local
# Set NEXT_PUBLIC_ORA_OWNER_TOKEN to the token printed above
npm install
npm run dev   # → http://localhost:3100
```

> **Security note: localhost only.** `NEXT_PUBLIC_ORA_OWNER_TOKEN` is visible in the
> JavaScript bundle. This is intentional for single-owner localhost use only. A hosted
> deployment needs a proper login flow: server-side session, HttpOnly cookie, Secure +
> SameSite=Strict. Do not expose this on a shared network without replacing auth.

---

## Configuration

### Environment variables (`backend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | **Yes** | Your Anthropic API key |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `http://localhost:3100`) |
| `ORA_HA_URL` | No | Home Assistant URL (e.g. `http://homeassistant.local:8123`) |
| `ORA_HA_TOKEN` | No | Home Assistant long-lived access token |
| `ORA_OWNER_TOKEN` | No | Pin the owner token across restarts |
| `ORA_MCP_ENABLED` | No | Set to `false` to disable the MCP server (default: `true`) |
| `ORA_HTTPS` | No | Set to `1` for production HTTPS cookie flags |
| `ORA_ATOMICMAIL_DIR` | No | AtomicMail data directory (default: `~/.atomicmail/`; contains credentials.json, session.jwt, capability.jwt) |

### Policy enforcement modes

Set via the Trust Center UI or `PUT /policy/mode`:

| Mode | Behaviour |
|------|-----------|
| `enforced` | Rules apply, actions held or denied as configured **(default)** |
| `audit_only` | All actions allowed but every verdict is still logged |
| `permissive` | All actions allowed, minimal logging |

### Agent model (`backend/config.yaml`)

The agent model is configured in `config.yaml`. Default: `claude-sonnet-4-6`.

---

## Email integration (AtomicMail)

Domestic Oracle ships with built-in email via [AtomicMail](https://atomicmail.ai), an agent-native email service built on [JMAP](https://jmap.io/) (RFC 8620/8621).

### One-time setup

```bash
npx --package=@atomicmail/agent-skill atomicmail register --username oracle
```

This runs a proof-of-work registration and writes credentials to `~/.atomicmail/credentials.json`. The Oracle picks them up automatically on next start.

### What it does

| Tool | Tier | What it does |
|------|------|-------------|
| `send_email` | GUARDED | Sends an email; held for approval by default policy |
| `reply_to_email` | GUARDED | Replies to a message by ID; held for approval |
| `read_inbox` | SAFE | Reads recent inbox; logged but never held |

Outgoing email goes through the same consent gate as device commands and purchases. The default seed policy blocks all outgoing email until the owner approves each message in the Trust Center. Change this by adding a permissive rule via `POST /policies` or the Trust Center UI.

### Example policy: auto-allow email to a trusted address

```json
{
  "rule_type": "action_deny",
  "params": { "action": "send_email" }
}
```

Replace with a `time_window` or `spend_limit` variant to allow email within specific windows. See the [API reference](#api-reference) for policy schema.

---

## Trust Center

The Trust Center (top-right drawer in the UI) is your live control panel:

| Tab | What it shows |
|-----|--------------|
| **Pending** | Actions held for your approval, with one-click approve or deny |
| **Ledger** | Every action with verdict, risk score, provenance, and timestamp |
| **Devices** | Smart home devices discovered via Home Assistant |
| **Policies** | Active rules; add new rules in natural language or as structured JSON |
| **Agents** | Registered actors and their status; revoke or restore access instantly |
| **Keys** | Signing key status, rotation history, offline backup export |

---

## Testing

The test suite covers every major behavioral contract:

```
tests/
├── test_crypto.py          Ed25519 sign/verify, nonce replay, key rotation
├── test_ledger.py          Hash chain, anchor, rollback detection
├── test_policy.py          Spend limits, time windows, action deny, enforcement modes
├── test_consent.py         Gate verdicts, agent identity, approval lifecycle
├── test_provenance.py      Injection pattern scanner (6 categories, 26 tests)
├── test_anchor.py          External anchor tamper detection
├── test_agent_identity.py  Signed agent requests, revocation, replay rejection
├── test_key_rotation.py    Cross-rotation chain verification
├── test_sessions.py        Session create/validate/revoke/prune
└── test_routes.py          HTTP layer via FastAPI TestClient (auth, approvals, policies)
```

```bash
cd backend
pip install pytest
pytest tests/ -v
# 126 tests, all passing
```

> `test_routes.py` requires the DeerFlow harness to be installed. On environments without
> it the route tests skip gracefully; the other 88 tests run anywhere.

---

## API reference

`[owner]` endpoints require a valid `ora_session` HttpOnly cookie obtained from `POST /auth/login`. The Oracle agent cannot present this cookie; it can only act through the consent gate.

```
POST   /auth/login                      Exchange passphrase for session cookie
POST   /auth/logout                     Revoke session
GET    /auth/session                    Session validity probe

GET    /health                          Liveness + readiness check

GET    /ledger                          Recent audit entries
GET    /ledger/verify                   Hash-chain integrity check
GET    /ledger/summary                  Rolling summary by category
GET    /ledger/export                   Full chain export with keyset (offline verification)

GET    /policies                        Active policies
POST   /policies              [owner]   Add a policy (structured or natural language)
DELETE /policies/{id}         [owner]   Remove a policy
GET    /policy/mode                     Current enforcement mode
PUT    /policy/mode           [owner]   Change enforcement mode
POST   /policy/dryrun                   Simulate policy evaluation (no side effects)

GET    /approvals             [owner]   Pending held actions
POST   /approvals/{id}/resolve[owner]   Approve or deny

GET    /agents                          Registered actors
POST   /agents/register       [owner]   Register an external signed agent
POST   /agents/{id}/revoke    [owner]   Block an actor immediately
POST   /agents/{id}/restore   [owner]   Re-enable an actor

GET    /devices                         Home Assistant devices
POST   /devices/control       [owner]   Control a device through the consent gate

GET    /keys/status           [owner]   Signing key info and rotation history
POST   /keys/rotate           [owner]   Generate a new signing key
GET    /keys/backup           [owner]   Export private key hex for offline backup (logged)

GET    /mcp/info              [owner]   MCP server status, tools, and client config
GET    /provenance/patterns   [owner]   Injection pattern registry

POST   /chat                            Stream a reply over Server-Sent Events
GET    /history/{user_id}               Recent conversation thread
```

---

## Frequently asked questions

**Does it work without Home Assistant?**
Yes. A mock home module is used when `ORA_HA_URL` is not set. All device control actions still flow through the consent gate and are logged to the ledger.

**Can I use a different LLM?**
The agent is Claude Sonnet 4 via the Anthropic API. The DeerFlow harness supports other models through `config.yaml`; see DeerFlow's documentation for the model configuration format.

**What happens if I revoke the agent while an action is pending?**
Identity is checked at the gate, not at scheduling time. If the agent is revoked before it submits another action, that action is denied. Actions already in the approval queue submitted before revocation can still be approved or denied by the owner.

**Is my data sent to Anthropic?**
Conversation content and tool inputs are sent to the Anthropic API to generate responses. Ledger entries, policies, sessions, agent keys, and approval records are stored locally in SQLite and never leave your server.

**Can multiple people use it?**
The current auth model is single-owner (one passphrase, one session cookie). Multi-user household support is on the roadmap.

**How does the cryptographic audit ledger work?**
Every action appended to the ledger includes: actor ID, action, arguments, policy decision, outcome, and timestamp. The entry is SHA-256 hashed and the hash is chained to the previous entry, then the whole entry is signed with the server's Ed25519 private key. `GET /ledger/verify` walks the entire chain and returns `{"valid": true}` if no entry has been tampered with. An anchor file provides an external reference to detect truncation or rollback attacks.

**Can I verify the ledger without running the server?**
Yes. `GET /ledger/export` returns the full chain and keyset. The `tools/verify_ledger.py` script verifies signatures and hash links offline using only the exported JSON and standard Python libraries.

**How is this different from Open Interpreter or similar projects?**
Open Interpreter and similar tools focus on capability: giving an LLM the ability to run code and control a computer. Domestic Oracle focuses on **governed capability**: the same actions, but with a mandatory policy layer, approval queue, and cryptographic audit trail. Every action is logged and policy-constrained by design, not by convention.

**What is the MCP server for?**
The built-in MCP (Model Context Protocol) server exposes Domestic Oracle's governance tools (ledger read, policy query, approval resolution) as MCP tools. Any MCP-compatible client (Claude Desktop, etc.) can connect to it and interact with the governance layer directly.

---

## Roadmap

- [ ] Docker / one-command setup
- [ ] Telegram front-end
- [ ] Scheduled recurring tasks ("every morning at 8am, summarise my emails")
- [x] Email integration (AtomicMail JMAP)
- [ ] Calendar integration
- [ ] Multi-user household support
- [ ] Published MCP tool registry listing

---

## Contributing

Issues and pull requests are welcome. Run the test suite before submitting:

```bash
cd backend && pytest tests/ -v
```

---

## License

MIT. See [LICENSE](LICENSE).

---

## Related projects

- [DeerFlow](https://github.com/bytedance/deer-flow): the LangGraph agent harness powering the Oracle agent
- [Home Assistant](https://www.home-assistant.io/): the smart home platform integrated for device control
- [Model Context Protocol](https://modelcontextprotocol.io/): the MCP standard the built-in server implements

---

*Domestic Oracle is an independent open-source project. It is not affiliated with Anthropic, Home Assistant, or ByteDance.*
