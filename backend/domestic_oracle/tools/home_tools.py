"""
Guarded home-control tool for Domestic Oracle.

Every device command routes through the consent gate before touching Home Assistant.
The gate evaluates active policies, writes a signed ledger entry, and either:
  - executes immediately (allow)
  - parks the action for owner approval (hold)
  - blocks outright (deny)
"""
from langchain_core.tools import tool


@tool
def control_device(device: str, action: str) -> str:
    """Control a smart home device — light, switch, lock, thermostat, fan, or cover.

    Device is a friendly name ('Living Room Lamp') or entity_id ('light.living_room').
    Action is one of: on, off, toggle, lock, unlock, open, close.
    Use discover_devices first if you are unsure of the exact device name.
    All commands pass through the owner's consent gate and are logged to the audit ledger.
    """
    import consent
    import home_assistant

    result = consent.request_action(
        actor_id="oracle.agent",
        action="control_device",
        args={"device": device, "action": action},
        execute=lambda: home_assistant.control(device, action),
    )
    if result["status"] == "executed":
        return (
            f"Done. {result['result']} "
            f"(Audit ledger #{result['ledger_id']})"
        )
    if result["status"] == "held":
        return (
            f"Held for owner approval (approval #{result['approval_id']}). "
            f"Reason: {result['reason']}. "
            f"The action has NOT been performed yet — the owner must approve it in the "
            f"Trust Center. Please let the user know."
        )
    return f"Action blocked by policy. Reason: {result['reason']}."
