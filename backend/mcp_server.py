"""
Domestic Oracle MCP server.

Exposes Domestic Oracle's governed tools via the Model Context Protocol so any
MCP-compatible client (Claude Desktop, Cursor, Zed, etc.) can control the
home through the same consent gate, policy engine, and tamper-evident ledger
as the built-in chat interface.

Transport: SSE, mounted at /mcp on the main FastAPI app.

Guarded tools → consent.request_action() → policy → ledger → optional HA call.
  Actor id: "ora.mcp" (pre-trusted internal actor).
  High-risk or policy-blocked actions are held in the approval queue.

Safe tools are read-only and bypass the gate entirely.

Auth: if ORA_MCP_TOKEN is set, the token must be provided in the
  X-Ora-Mcp-Token request header or ?token= query param on the SSE
  connection.  When unset, network-level auth (nginx, VPN) is assumed.
"""
from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

import consent
import home_assistant as ha
import ledger
import policy
import provenance

MCP_ACTOR = "ora.mcp"

TOOLS_META = [
    {"name": "control_device",  "guarded": True,
     "description": "Control a smart-home device (light, lock, switch, cover…)"},
    {"name": "make_purchase",   "guarded": True,
     "description": "Request a purchase — gated and logged"},
    {"name": "send_message",    "guarded": True,
     "description": "Send a message on the user's behalf — gated and logged"},
    {"name": "list_devices",    "guarded": False,
     "description": "List available devices and current states (read-only)"},
    {"name": "query_ledger",    "guarded": False,
     "description": "Return recent audit ledger entries (read-only)"},
    {"name": "check_policy",    "guarded": False,
     "description": "Dry-run an action against the active policies (read-only)"},
]

mcp = FastMCP(
    name="EchoBond",
    instructions=(
        "You are connected to EchoBond, a governed home AI assistant. "
        "Every consequential action is gated by a policy engine and recorded "
        "in a tamper-evident cryptographic ledger. "
        "Guarded tools (control_device, make_purchase, send_message) may be "
        "held for owner approval if policy requires it. "
        "Read-only tools (list_devices, query_ledger, check_policy) have no "
        "side effects and do not require approval."
    ),
)


# ── Guarded tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def control_device(entity_id: str, command: str) -> str:
    """Control a smart-home device through the EchoBond consent gate.

    The action is policy-evaluated, signed, and appended to the audit ledger.
    If policy holds it, the owner receives an approval request.

    Args:
        entity_id: Home Assistant entity ID, e.g. "light.kitchen" or "lock.front_door".
        command: Action to perform, e.g. "on", "off", "lock", "unlock", "open", "close".
    """
    result = consent.request_action(
        actor_id=MCP_ACTOR,
        action="control_device",
        args={"entity": entity_id, "command": command},
        execute=lambda: ha.control(entity_id, command),
        sources=[{"type": provenance.SOURCE_MCP, "id": f"mcp:control_device:{entity_id}"}],
    )
    return _gate_message(result)


@mcp.tool()
def make_purchase(item: str, amount: float, vendor: str = "") -> str:
    """Request a purchase through the EchoBond consent gate.

    High-value purchases may require explicit owner approval before executing.

    Args:
        item: Description of what to buy.
        amount: Price in USD.
        vendor: Optional vendor name or URL.
    """
    args: dict = {"item": item, "amount": amount}
    if vendor:
        args["vendor"] = vendor

    result = consent.request_action(
        actor_id=MCP_ACTOR,
        action="make_purchase",
        args=args,
        execute=lambda: f"Purchase queued: {item} ${amount:.2f}{' from ' + vendor if vendor else ''}.",
        sources=[{"type": provenance.SOURCE_MCP, "id": "mcp:make_purchase"}],
    )
    return _gate_message(result)


@mcp.tool()
def send_message(recipient: str, body: str) -> str:
    """Send a message on the user's behalf through the EchoBond consent gate.

    The message body is also scanned for injection signals before the gate runs.

    Args:
        recipient: Name, username, or address of the intended recipient.
        body: Message text.
    """
    result = consent.request_action(
        actor_id=MCP_ACTOR,
        action="send_message",
        args={"recipient": recipient, "body": body},
        execute=lambda: f"Message to {recipient}: queued (no outbox configured).",
        sources=[{
            "type": provenance.SOURCE_MCP,
            "id": "mcp:send_message",
            "content": body,
        }],
    )
    return _gate_message(result)


# ── Safe (read-only) tools ────────────────────────────────────────────────────

@mcp.tool()
def list_devices() -> str:
    """List all smart-home devices and their current states.

    Returns a JSON array of {entity_id, name, state, domain} objects.
    No side effects; this call bypasses the consent gate.
    """
    try:
        devices = ha.list_devices()
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(devices, indent=2)


@mcp.tool()
def query_ledger(limit: int = 20) -> str:
    """Return recent audit ledger entries.

    Shows what actions EchoBond has taken, held, or denied. Each entry
    includes the actor, action, decision, and a short outcome.
    No side effects; this call bypasses the consent gate.

    Args:
        limit: Number of most-recent entries to return (1–100).
    """
    limit = max(1, min(100, int(limit)))
    entries = ledger.list_entries(limit)
    slim = [
        {
            "id":       e["id"],
            "ts":       e["ts"],
            "actor":    e["actor_id"],
            "action":   e["action"],
            "summary":  e["args_summary"],
            "decision": e["decision"],
            "status":   e["status"],
            "outcome":  e["outcome"],
        }
        for e in entries
    ]
    return json.dumps(slim, indent=2)


@mcp.tool()
def check_policy(action: str, args_json: str = "{}") -> str:
    """Dry-run an action against the active policies without executing it.

    Useful for pre-checking whether a planned action would be allowed, held,
    or denied before committing.

    Args:
        action: Action name, e.g. "control_device" or "make_purchase".
        args_json: JSON-encoded arguments, e.g. '{"amount": 150}'.
    """
    try:
        args = json.loads(args_json)
    except (ValueError, TypeError):
        return json.dumps({"error": "args_json is not valid JSON"})

    decision, reason = policy.evaluate(MCP_ACTOR, action, args)
    return json.dumps({"action": action, "verdict": decision, "reason": reason})


# ── Token guard ASGI middleware ───────────────────────────────────────────────

class _TokenGuard:
    """Wrap the MCP Starlette app with a bearer-token check.

    Reads token from X-Ora-Mcp-Token header or ?token= query param.
    Only active when ORA_MCP_TOKEN is set in the environment.
    """

    def __init__(self, app, token: str) -> None:
        self._app   = app
        self._token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            # Check query string first
            qs_raw = scope.get("query_string", b"").decode()
            provided = None
            for part in qs_raw.split("&"):
                k, _, v = part.partition("=")
                if k == "token":
                    provided = v
                    break
            # Fall back to header
            if not provided:
                for k, v in scope.get("headers", []):
                    if k.lower() == b"x-ora-mcp-token":
                        provided = v.decode()
                        break

            if provided != self._token:
                if scope["type"] == "http":
                    await send({
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b'{"error":"Invalid or missing MCP token"}',
                    })
                return

        await self._app(scope, receive, send)


def build_asgi_app(mount_path: str = "/mcp"):
    """Return an ASGI app for the MCP SSE server, optionally token-guarded."""
    starlette_app = mcp.sse_app(mount_path=mount_path)
    token = os.environ.get("ORA_MCP_TOKEN", "").strip()
    if token:
        return _TokenGuard(starlette_app, token)
    return starlette_app
