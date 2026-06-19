"""
Domestic Oracle API gateway.

Combines the trust-layer control plane with an Oracle-powered /chat endpoint:

Chat:
  POST   /chat               stream a reply over Server-Sent Events (DeerFlow agent)
  GET    /history/{user_id}  recent conversation thread info
  DELETE /memory/{user_id}   wipe Oracle memory for a user  [owner]
  GET    /health             liveness check

Trust layer (all routes from Ora, unchanged):
  GET    /ledger             recent audit entries
  GET    /ledger/verify      chain integrity check
  GET    /ledger/summary     rolling rollup by category, holds, and risk
  GET    /policies           active policies
  POST   /policies           add a policy
  DELETE /policies/{id}      remove a policy
  GET    /policy/mode        enforcement posture
  PUT    /policy/mode        change the posture  [owner]
  GET    /devices            smart-home devices
  POST   /devices/control    control a device through the gate  [owner]
  GET    /agents             registered actors
  POST   /agents/{id}/revoke    block an actor  [owner]
  POST   /agents/{id}/restore   re-enable an actor  [owner]
  GET    /approvals          actions held pending the user's okay
  POST   /approvals/{id}/resolve   approve or deny  [owner]
  POST   /agents/register    register an external agent  [owner]
  POST   /agent/act          governed entry point for external agents

[owner] endpoints require the X-Ora-Owner header. The Oracle agent can never present it.
"""
import os, json, sys
from pathlib import Path
from collections import OrderedDict, deque

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
load_dotenv()

# Add backend dir to path so Ora's modules (consent, ledger, etc.) are importable
_BACKEND = Path(__file__).parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import ledger
import policy
import consent
import auth
import home_assistant as ha
from auth import require_owner

# Initialise consent DB (also creates the nonces table) and owner token.
consent.init_db()
auth.owner_token()

# Import Oracle client singleton AFTER consent is initialised (executor registration
# happens inside get_client() → _register_executor(), which needs consent to be ready).
from domestic_oracle.agent import get_client
from domestic_oracle.stream import deerflow_to_sse

from anthropic import Anthropic as _Anthropic

MAX_HISTORY = 20
MAX_USERS = 500

_history: OrderedDict[str, deque] = OrderedDict()
_claude: "_Anthropic | None" = None


def _get_history(user_id: str) -> deque:
    if user_id in _history:
        _history.move_to_end(user_id)
    else:
        if len(_history) >= MAX_USERS:
            _history.popitem(last=False)  # evict LRU
        _history[user_id] = deque(maxlen=MAX_HISTORY)
    return _history[user_id]


def _get_claude() -> "_Anthropic | None":
    global _claude
    if _claude is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if key:
            _claude = _Anthropic(api_key=key)
    return _claude

app = FastAPI(title="Domestic Oracle", version="1.0.0")

_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3100,http://localhost:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================ Pydantic models
class ChatRequest(BaseModel):
    user_id: str = "default"
    message: str


class PolicyRequest(BaseModel):
    text: str | None = None
    rule_type: str | None = None
    params: dict | None = None
    label: str | None = None


class ResolveRequest(BaseModel):
    decision: str


class ModeRequest(BaseModel):
    mode: str


class DeviceControlRequest(BaseModel):
    device: str
    action: str


class RegisterAgentRequest(BaseModel):
    name: str


class AgentActRequest(BaseModel):
    actor_id: str
    action: str
    args: dict = {}
    nonce: str
    ts: float
    sig: str


# ================================================================ Chat
@app.get("/health")
def health():
    return {"status": "ok", "agent": "domestic-oracle"}


@app.get("/history/{user_id}")
def get_history(user_id: str):
    return {"user_id": user_id, "messages": list(_get_history(user_id))}


@app.delete("/memory/{user_id}")
def wipe_memory(user_id: str, _owner: bool = Depends(require_owner)):
    """Wipe DeerFlow's persisted memory for a user. Thread checkpoints are kept."""
    from deerflow.agents.memory.storage import get_memory_storage
    storage = get_memory_storage()
    storage.save({}, user_id=user_id)
    _get_history(user_id).clear()
    return {"status": "wiped", "user_id": user_id}


@app.post("/chat")
async def chat(req: ChatRequest):
    user_id = req.user_id or "default"
    message = req.message.strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    _get_history(user_id).append({"role": "user", "content": message})
    thread_id = f"eb-{user_id}"

    async def event_stream():
        client = get_client()
        full_reply_parts: list[str] = []

        try:
            events = client.stream(message, thread_id=thread_id)
            async for frame in deerflow_to_sse(events):
                yield f"data: {json.dumps(frame)}\n\n"
                if "text" in frame:
                    full_reply_parts.append(frame["text"])
        except Exception as e:
            error_text = f"(Something went wrong: {e})"
            yield f"data: {json.dumps({'text': error_text})}\n\n"
            full_reply_parts.append(error_text)

        full_reply = "".join(full_reply_parts)
        if full_reply:
            _get_history(user_id).append({"role": "assistant", "content": full_reply})

        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ================================================================ Ledger
@app.get("/ledger")
def get_ledger(limit: int = 100):
    return {"entries": ledger.list_entries(limit=limit)}


@app.get("/ledger/verify")
def verify_ledger(full: bool = False):
    return ledger.integrity(full=full)


@app.get("/ledger/summary")
def ledger_summary(days: int = 7):
    return ledger.summary(days)


# ================================================================ Policies
@app.get("/policies")
def get_policies():
    return {"policies": policy.list_policies()}


@app.get("/policy/mode")
def get_policy_mode():
    return {"mode": policy.get_mode()}


@app.put("/policy/mode")
def set_policy_mode(req: ModeRequest, _owner: bool = Depends(require_owner)):
    try:
        return {"mode": policy.set_mode(req.mode)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/policies")
def create_policy(req: PolicyRequest, _owner: bool = Depends(require_owner)):
    if req.rule_type and req.params is not None:
        saved = policy.add_policy(req.rule_type, req.params, label=req.label or "")
        return {"policy": saved}
    if req.text:
        claude = _get_claude()
        if not claude:
            return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=500)
        try:
            rule = policy.parse_policy(req.text, claude)
            saved = policy.add_policy(rule["rule_type"], rule["params"],
                                      label=rule.get("label", req.text), source="manual")
            return {"policy": saved}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"error": "Provide either text or (rule_type + params)."}, status_code=400)


@app.delete("/policies/{policy_id}")
def remove_policy(policy_id: int, _owner: bool = Depends(require_owner)):
    ok = policy.delete_policy(policy_id)
    return {"deleted": ok, "id": policy_id}


# ================================================================ Agents
@app.get("/agents")
def get_agents():
    return {"agents": consent.list_agents()}


@app.post("/agents/register")
def register_agent(req: RegisterAgentRequest, _owner: bool = Depends(require_owner)):
    return consent.register_agent(req.name)


@app.post("/agents/{agent_id}/revoke")
def revoke_agent(agent_id: str, _owner: bool = Depends(require_owner)):
    ok = consent.set_agent_status(agent_id, "revoked")
    return {"ok": ok, "agent_id": agent_id, "status": "revoked"}


@app.post("/agents/{agent_id}/restore")
def restore_agent(agent_id: str, _owner: bool = Depends(require_owner)):
    ok = consent.set_agent_status(agent_id, "active")
    return {"ok": ok, "agent_id": agent_id, "status": "active"}


# ================================================================ Agent broker (external signed agents)
@app.post("/agent/act")
def agent_act(req: AgentActRequest):
    """Governed entry point for EXTERNAL agents — must present a signed request."""
    ok, why = consent.verify_agent_request(
        req.actor_id, req.action, req.args, req.nonce, req.ts, req.sig
    )
    if not ok:
        return JSONResponse({"error": f"Rejected: {why}"}, status_code=401)

    # Ensure DeerFlow client is running (registers the guarded-action executor).
    get_client()
    if consent._executor is None:  # type: ignore[attr-defined]
        return JSONResponse({"error": "Agent executor not registered."}, status_code=503)

    _action, _args = req.action, req.args
    result = consent.request_action(
        actor_id=req.actor_id,
        action=_action,
        args=_args,
        execute=lambda: consent._executor(_action, _args),  # type: ignore[misc]
    )
    return {"result": result.get("result"), "status": result["status"],
            "approval_id": result.get("approval_id")}


# ================================================================ Devices
@app.get("/devices")
def get_devices():
    return {"configured": ha.configured(), "devices": ha.list_devices()}


@app.post("/devices/control")
def control_device_endpoint(req: DeviceControlRequest, _owner: bool = Depends(require_owner)):
    """Control a device from the Trust Center — still routed through the consent gate."""
    result = consent.request_action(
        actor_id="ora.home",
        action="control_device",
        args={"device": req.device, "action": req.action},
        execute=lambda: ha.control(req.device, req.action),
    )
    if result["status"] == "executed":
        return {"text": result["result"], "approval": None}
    if result["status"] == "held":
        return {"text": f"Held for approval (#{result['approval_id']}).",
                "approval": {"id": result["approval_id"], "reason": result["reason"]}}
    return {"text": f"Blocked: {result['reason']}", "approval": None}


# ================================================================ Approvals
@app.get("/approvals")
def get_approvals():
    return {"approvals": consent.list_pending()}


@app.post("/approvals/{approval_id}/resolve")
def resolve_approval(approval_id: str, req: ResolveRequest,
                     _owner: bool = Depends(require_owner)):
    decision = "approve" if req.decision.lower() in ("approve", "yes", "ok", "allow") else "deny"
    return consent.resolve(approval_id, decision)
