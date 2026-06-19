"""
Read-only safe tools for Domestic Oracle.

These are SAFE tier: no consent gate hold/deny, but every call is still logged
to the audit ledger so the owner has a complete picture of what the agent looked at.
"""
from langchain_core.tools import tool


@tool
def discover_devices() -> str:
    """List all smart home devices Domestic Oracle can see, with their current state.

    Use this before control_device to find the exact name or entity_id of a device.
    Read-only — no consent gate, but logged to the audit ledger.
    """
    import ledger
    import risk
    import home_assistant as ha

    devices = ha.list_devices()
    if not devices:
        result = "I don't see any smart-home devices."
    else:
        where = "your Home Assistant" if ha.configured() else "a mock home (no Home Assistant configured yet)"
        lines = [f"Devices in {where}:"]
        for d in devices:
            lines.append(f"- {d['name']} ({d['entity_id']}) — currently {d['state']}")
        result = "\n".join(lines)

    ledger.append(
        "oracle.agent", "discover_devices", "(no args)",
        "allow", "executed", result[:300],
        risk.category("discover_devices"), risk.score("discover_devices", {}, "allow"),
    )
    return result
