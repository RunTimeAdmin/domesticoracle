"""
Domestic Oracle agent client — the brain inside the cage.

OracleClient is initialised once as a module-level singleton. Configuration
points at config.yaml (Claude Sonnet 4, Domestic Oracle tools registered in
the tools: section, SQLite checkpointer under .deer-flow/data/).

The consent gate executor is also registered here so that when the owner approves
a held action via the Trust Center, the action is re-run through the correct handler.
"""
import os
import sys
from pathlib import Path

# Ensure the backend directory is on sys.path so `import consent` etc. work when
# the client imports domestic_oracle.tools.* during tool loading.
_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Tell the underlying runtime where to find config.yaml and where to write state files.
_CONFIG_PATH = str(_BACKEND_DIR / "config.yaml")
os.environ.setdefault("DEER_FLOW_CONFIG_PATH", _CONFIG_PATH)
os.environ.setdefault("DEER_FLOW_PROJECT_ROOT", str(_BACKEND_DIR))

from domestic_oracle.core import OracleClient  # noqa: E402

_client: OracleClient | None = None


def get_client() -> OracleClient:
    """Return the process-wide Oracle client, creating it on first call."""
    global _client
    if _client is None:
        _client = OracleClient(
            config_path=_CONFIG_PATH,
            thinking_enabled=True,
            subagent_enabled=False,
            plan_mode=False,
        )
        _register_executor()
    return _client


def _register_executor() -> None:
    """Register the re-execution hook with the consent gate.

    When the owner approves a held action in the Trust Center, consent.resolve()
    calls this executor to actually run the guarded handler. It mirrors the
    guarded tool handlers without going back through the full consent gate.
    """
    import consent
    import home_assistant

    def _execute(action: str, args: dict) -> str:
        args = args or {}
        if action == "control_device":
            return home_assistant.control(
                args.get("device", ""), args.get("action", "")
            )
        if action == "make_purchase":
            item   = args.get("item", "unknown item")
            amount = float(args.get("amount", 0))
            vendor = args.get("vendor", "an online vendor")
            return f"[SIMULATED] Ordered '{item}' from {vendor} for ${amount:.2f}."
        if action == "send_message":
            recipient = args.get("recipient", "someone")
            body      = args.get("body", "")
            preview   = body if len(body) <= 80 else body[:77] + "..."
            return f"[SIMULATED] Sent a message to {recipient}: \"{preview}\""
        if action == "send_email":
            from domestic_oracle import atomicmail_client as _am
            return _am.send(
                args.get("to", ""),
                args.get("subject", ""),
                args.get("body", ""),
            )
        if action == "reply_to_email":
            from domestic_oracle import atomicmail_client as _am
            return _am.reply(args.get("email_id", ""), args.get("body", ""))
        raise ValueError(f"No guarded handler for action: {action!r}")

    consent.set_executor(_execute)
