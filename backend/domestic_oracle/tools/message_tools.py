"""
Guarded messaging tool for Domestic Oracle.

Messages are simulated in Phase 1. The default policy holds all outgoing messages
for the owner's approval (recipient_block: all_contacts).
"""
from langchain_core.tools import tool


@tool
def send_message(recipient: str, body: str) -> str:
    """Send a message to someone as the user (SIMULATED — no real message sent in Phase 1).

    Goes through the consent gate. The default policy holds all messages for owner
    approval before they are sent. Logged to the audit ledger.
    """
    import consent

    def _execute():
        preview = body if len(body) <= 80 else body[:77] + "..."
        return f"[SIMULATED] Sent a message to {recipient}: \"{preview}\""

    result = consent.request_action(
        actor_id="oracle.agent",
        action="send_message",
        args={"recipient": recipient, "body": body},
        execute=_execute,
    )
    if result["status"] == "executed":
        return (
            f"Done. {result['result']} "
            f"(Audit ledger #{result['ledger_id']})"
        )
    if result["status"] == "held":
        return (
            f"Message held for owner approval (approval #{result['approval_id']}). "
            f"Reason: {result['reason']}. "
            f"The message has NOT been sent — the owner must approve in the Trust Center."
        )
    return f"Message blocked by policy. Reason: {result['reason']}."
