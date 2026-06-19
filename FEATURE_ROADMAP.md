# Domestic Oracle — Feature Roadmap

## Who this is for

The addressable install base right now is a narrow but well-defined Venn intersection:
people who already run Home Assistant, who are comfortable with Docker, and who have
been burned by or are actively worried about ungoverned LLM agents pointed at their
home. Globally that's roughly 5,000–20,000 people today, growing as LLM home
automation matures from hobbyist to mainstream. That's the right starting size for
a trust-establishing product. The pitch isn't "everyone who wants a home assistant" —
it's "the HA user who already knows why giving an LLM unchecked service-call access
is a bad idea."

## The actual competitive threat

Not Alexa. Not Google Home. Those are cloud-tethered, closed, and they'd never show
you a tamper-evident ledger.

The real comparison is to the DIY AutoGPT / LangChain agent projects that HA
enthusiasts wire up themselves. Capable, ungoverned, a little frightening. The danger
isn't "zero governance in the abstract" — it's something more specific: when a local
LLM fires a service call, there's no record of *why*. No chain of reasoning, no policy
that permitted it, nothing to dispute if something goes wrong. The owner has no way to
know whether an action was deliberate or hallucinated.

Domestic Oracle's answer: **when something goes wrong in your house at 3am, you'll
know exactly what Ora decided and why — and if you wouldn't have authorized it,
you'll know that too.**

Josh.ai ($250/month, professional AV installation) is not a competitor. Cutting it
from the comparison tightens the pitch.

---

## Priority ordering (impact × dependency)

The ordering is determined by two things: blast radius of the fix, and what each item
unblocks. Docker goes first because a moat nobody can reach defends nothing. Auth goes
second — not just because it closes the localhost-only hole, but because push-approvals
structurally depends on it (a Telegram bot approving actions needs a server-side
credential model, not a browser-baked token). Push approvals is the screenshot feature:
the thing that makes the hold-queue feel like a superpower. Everything after that is
sequencing.

| # | Feature | Category | Priority | Complexity |
|---|---------|----------|----------|------------|
| 1 | One-command Docker setup | Ops | **High** | Moderate |
| 2 | Session auth (HttpOnly cookie) | Security | **High** | Moderate |
| 3 | Push approvals via Telegram/Signal | UX | **High** | Moderate |
| 4 | Ledger export + standalone verifier | Security | **High** | Easy |
| 5 | Rate limit + daily blast-radius cap | Security | **High** | Moderate |
| 6 | Key rotation / backup / recovery | Ops | **High** | Moderate |
| 7 | External anchor (timestamp / WORM) | Security | Medium | Moderate |
| 8 | Prompt-injection provenance tagging | Security | Medium | Complex |
| 9 | Policy dry-run ("what would happen if…") | UX | Medium | Easy |
| 10 | Weekly activity digest | UX | Medium | Easy |
| 11 | Mobile-responsive Trust Center | UX | Medium | Easy |
| 12 | Scheduled tasks through the gate | Integrations | Medium | Moderate |
| 13 | MCP server exposure | Integrations | Medium | Complex |
| 14 | Email / calendar connectors (read-first) | Integrations | Medium | Moderate |
| 15 | Observability + scheduled self-verify | Ops | Medium | Moderate |
| 16 | Optional Postgres backend | Scalability | Low | Moderate |

---

## Feature specs

### 1 — One-command Docker setup ✅ done
`docker compose up --build` brings up backend (FastAPI + uvicorn) and frontend
(Next.js standalone). All mutable state (oracle.db, oracle_keys/, anchor.log,
oracle_memory.json, .deer-flow/) lives in a named volume (`ora_data:/data`) via the
`ORA_DATA_DIR` env var. First-run script generates the owner token; subsequent runs
keep it.

### 2 — Session auth (HttpOnly cookie)
**Status:** spec complete, implementation ready to ship.

Replaces the `NEXT_PUBLIC_ORA_OWNER_TOKEN` model (baked into the JS bundle) with
proper server-side sessions.

**Flow:**
```
POST /auth/login  { passphrase: "..." }
  → verify against ORA_OWNER_TOKEN (constant-time)
  → sessions.create() → INSERT INTO sessions (token, expires_at)
  → Set-Cookie: ora_session=<token>; HttpOnly; SameSite=Strict; [Secure]
  ← { ok: true }

All [owner] endpoints
  → require_owner dep reads Cookie: ora_session
  → sessions.validate() → SELECT + sliding-window UPDATE
  → 401 if missing or expired

POST /auth/logout
  → sessions.revoke(token)
  → Delete-Cookie: ora_session
```

**Key design decisions:**
- Sessions are persisted to SQLite (`sessions` table) — survive restarts and work
  across multiple workers. In-memory dict dies on every reload.
- Sliding window (8h TTL resets on each validated request) — active owners stay
  logged in; idle sessions expire automatically.
- `SameSite=Strict` — handles CSRF for a directly-visited SPA. No double-submit
  needed for single-owner LAN deployment.
- `Secure` flag gated on `ORA_HTTPS ∈ {1, true, yes}` — explicit string check.
  `bool("0")` is `True` in Python; `bool(os.getenv("ORA_HTTPS"))` would set Secure
  even when ORA_HTTPS=0 and silently break cookies over plain HTTP.

**Sharp edge to document before #3 ships:** `SameSite=Strict` blocks the cookie on
top-level navigations from a foreign origin. Push-approvals (#3) will send deep links
(e.g. from Telegram) that click through to `/approvals/...`. The first request lands
unauthenticated. Fix: `SameSite=Lax` on the `/approvals/*` path, or a short-lived
URL token embedded in the link. Flag raised here; resolve it when #3 is built.

**Files changed:**
- `backend/sessions.py` — new: durable SQLite session store
- `backend/auth.py` — `require_owner` reads Cookie, not Header; adds `verify_passphrase`,
  `HTTPS` flag
- `backend/main.py` — `POST /auth/login`, `POST /auth/logout`; startup calls
  `sessions.init_table()` + `sessions.prune()`
- `frontend/lib/api.ts` — remove `OWNER_TOKEN` + `ownerHeaders()`; add
  `credentials: "include"` to all fetches
- `frontend/components/LoginModal.tsx` — new: passphrase form → POST /auth/login
- `frontend/app/page.tsx` — on mount, probe a protected endpoint; show LoginModal on 401
- `docker-compose.yml` — remove `NEXT_PUBLIC_ORA_OWNER_TOKEN` from frontend build args

### 3 — Push approvals via Telegram/Signal
A held action sitting in a web drawer nobody has open is a dead letter. This makes
the approval queue a superpower: the owner gets a message the moment Ora holds
something, with Approve / Deny inline buttons.

The Telegram bot holds the session token server-side. The owner never pastes it
anywhere. Auth (#2) is a prerequisite — the bot needs the durable session layer
to issue requests on the owner's behalf without replaying a browser token.

### 4 — Ledger export + standalone verifier ✅ done
`GET /ledger/export` returns the full chain + public key as JSON.
`tools/verify_ledger.py <export.json>` re-walks every hash and Ed25519 signature
with no app dependencies. The claim "tamper-evident" becomes evidence a skeptic
can check.

### 5 — Rate limit + daily blast-radius cap
A global circuit breaker inside `consent.request_action()`: max N guarded actions
per actor per hour, plus a configurable daily ceiling that, once hit, forces
everything to HOLD regardless of other policy. Prompt-injection or a looping agent
can currently fire a hundred allowed-but-small actions with nothing stopping it.

### 6 — Key rotation / backup / recovery
The integrity story rests on the Ed25519 key living outside the DB. Currently
undocumented: what happens when the disk dies, or the owner wants to rotate after
a scare? Required: a keyset model (new key signs new entries; old public key still
verifies historical entries), a backup export, and a recovery procedure. A security
person will ask this in the first five minutes; "unclear" kills the trust.

### 7 — External anchor (timestamp / WORM)
`anchor.log` catches rollback locally but can be deleted alongside the DB by anyone
with host access. An optional pluggable sink (RFC 3161 timestamping authority, S3
object-lock, or a public commit) makes rollback detectable by a third party. The
local file stays as the zero-config default.

### 8 — Prompt-injection provenance tagging
The agent reads the web and files (SAFE tools). A poisoned page can try to talk
the model into firing a guarded tool. Add structured "action provenance" metadata
so the ledger can record when a guarded action was triggered in a turn that ingested
untrusted external content. Let a policy rule HOLD-on-untrusted-context.

### 9 — Policy dry-run ✅ done
`POST /policy/dryrun { action, args }` simulates what policy would do for a
hypothetical action — no ledger entry, no approval queue entry, no side effects.
UI in Trust Center → Policies tab: action dropdown, contextual args fields,
colour-coded verdict (green/amber/red) with the reason string.

### 10 — Weekly activity digest
Rolling rollup (already computed by `ledger.summary`) delivered as a short readable
summary: actions taken, what was held, total spend, anything that scored high-risk.
Pushed via the same channel as approvals (#3). A boring weekly digest is the quiet
proof that the leash works.

### 11 — Mobile-responsive Trust Center
The control plane needs to work on a phone because that's where approvals will be
answered. If approving on a phone is painful, owners set everything to allow — and
the moat quietly switches off.

### 12 — Scheduled tasks through the gate
Scheduled actions must route through `request_action()` exactly like live ones: same
policy evaluation, same ledger entry, same hold behaviour. A scheduled purchase over
the cap should still wait for the owner. Unattended automation is exactly when
ungoverned agents do damage.

### 13 — MCP server exposure
Expose the consent gate as an MCP server so any MCP-aware client can request governed
actions. Every call still signed / policy-checked / ledger-logged. "The governed MCP
endpoint for your home" is a sharp, current, defensible position.

### 14 — Email / calendar connectors (read-first)
Read-only email/calendar tools are SAFE (logged, no gate). Any send/create is GUARDED.
"Ora can read your inbox all day but can't send a word as you without asking" is the
headline. The SAFE/GUARDED split handles this cleanly out of the box.

### 15 — Observability + scheduled self-verify
Extend `/health` into a real readiness probe (DB reachable, signing key loaded, anchor
writable). Run `verify_chain()` on a schedule and alert if integrity ever fails. A
governed system that doesn't tell you when its governance is broken isn't governed.

### 16 — Optional Postgres backend
`db.py` already abstracts connections. Add an optional Postgres backend for multi-user
households or always-on shared deployments that would hit SQLite's single-writer limit.
SQLite stays the zero-config default.
