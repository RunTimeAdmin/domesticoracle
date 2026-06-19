"""
Guarded purchase tool for Domestic Oracle.

Purchases are simulated in Phase 1 but exercise the full consent-and-audit pipeline.
The default policy holds any purchase over $50 for the owner's approval.
"""
from langchain_core.tools import tool


@tool
def make_purchase(item: str, amount: float, vendor: str = "an online vendor") -> str:
    """Purchase an item on the user's behalf (SIMULATED — no real money moves in Phase 1).

    Goes through the consent gate, which holds purchases over the configured spending
    limit for owner approval. Amount is in USD. Logged to the audit ledger.
    """
    import consent

    def _execute():
        return f"[SIMULATED] Ordered '{item}' from {vendor} for ${float(amount):.2f}."

    result = consent.request_action(
        actor_id="oracle.agent",
        action="make_purchase",
        args={"item": item, "amount": float(amount), "vendor": vendor},
        execute=_execute,
    )
    if result["status"] == "executed":
        return (
            f"Done. {result['result']} "
            f"(Audit ledger #{result['ledger_id']})"
        )
    if result["status"] == "held":
        return (
            f"Purchase held for owner approval (approval #{result['approval_id']}). "
            f"Reason: {result['reason']}. "
            f"Nothing has been ordered yet — the owner must approve in the Trust Center."
        )
    return f"Purchase blocked by policy. Reason: {result['reason']}."
