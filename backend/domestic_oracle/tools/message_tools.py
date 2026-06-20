"""
Messaging tools for Domestic Oracle.

send_message  — GUARDED: simulated IM/SMS; held by default policy (phase 1)
send_email    — GUARDED: real outgoing email via AtomicMail JMAP
reply_to_email — GUARDED: real email reply via AtomicMail JMAP
read_inbox    — SAFE: read-only inbox fetch; logged but not gate-held
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
        return f"Done. {result['result']} (Audit ledger #{result['ledger_id']})"
    if result["status"] == "held":
        return (
            f"Message held for owner approval (approval #{result['approval_id']}). "
            f"Reason: {result['reason']}. "
            f"The message has NOT been sent — the owner must approve in the Trust Center."
        )
    return f"Message blocked by policy. Reason: {result['reason']}."


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via the Oracle's AtomicMail inbox.

    Passes through the consent gate: the default policy holds all outgoing email
    for owner approval. Requires AtomicMail credentials (run the register CLI once).
    Logged to the audit ledger.
    """
    import consent
    from domestic_oracle import atomicmail_client as _am

    def _execute():
        return _am.send(to, subject, body)

    try:
        result = consent.request_action(
            actor_id="oracle.agent",
            action="send_email",
            args={"to": to, "subject": subject, "body": body},
            execute=_execute,
        )
    except _am.AtomicMailError as e:
        return f"Email unavailable: {e}"

    if result["status"] == "executed":
        return f"Done. {result['result']} (Audit ledger #{result['ledger_id']})"
    if result["status"] == "held":
        return (
            f"Email held for owner approval (approval #{result['approval_id']}). "
            f"Reason: {result['reason']}. "
            f"The email has NOT been sent — the owner must approve in the Trust Center."
        )
    return f"Email blocked by policy. Reason: {result['reason']}."


@tool
def reply_to_email(email_id: str, body: str) -> str:
    """Reply to an email in the Oracle's AtomicMail inbox by its message ID.

    Use read_inbox first to find the email_id. Passes through the consent gate.
    The original email is scanned for injection signals before the reply is queued.
    Logged to the audit ledger.
    """
    import consent
    import provenance as prov_mod
    from domestic_oracle import atomicmail_client as _am

    # Fetch and scan the original email content so the ledger records what
    # influenced this outgoing action. Injection signals force a HOLD even if
    # policy would otherwise allow.
    sources = None
    try:
        content = _am.get_email_preview(email_id)
        if content:
            sources = [{"type": "email", "id": email_id, "content": content}]
    except Exception:
        pass  # Don't fail the reply if scanning fails

    def _execute():
        return _am.reply(email_id, body)

    try:
        result = consent.request_action(
            actor_id="oracle.agent",
            action="reply_to_email",
            args={"email_id": email_id, "body": body},
            execute=_execute,
            sources=sources,
        )
    except _am.AtomicMailError as e:
        return f"Email reply unavailable: {e}"

    if result["status"] == "executed":
        return f"Done. {result['result']} (Audit ledger #{result['ledger_id']})"
    if result["status"] == "held":
        return (
            f"Reply held for owner approval (approval #{result['approval_id']}). "
            f"Reason: {result['reason']}. "
            f"The reply has NOT been sent — the owner must approve in the Trust Center."
        )
    return f"Reply blocked by policy. Reason: {result['reason']}."


_INBOX_CONTENT_CAP = 4000  # total chars returned to agent context
_PER_EMAIL_PREVIEW_CAP = 200  # chars of preview per message


@tool
def read_inbox(limit: int = 10) -> str:
    """Read recent emails from the Oracle's AtomicMail inbox.

    Returns a formatted list of the most recent messages (subject, sender, preview).
    Email content is scanned for injection signals before it reaches the agent.
    Read-only — no consent gate, but logged to the audit ledger with provenance.
    Requires AtomicMail credentials (run the register CLI once).
    """
    import ledger
    import provenance as prov_mod
    import risk
    from domestic_oracle import atomicmail_client as _am

    try:
        emails = _am.list_inbox(limit=max(1, min(limit, 50)))
    except _am.AtomicMailError as e:
        return f"Inbox unavailable: {e}"

    if not emails:
        prov = None
        result = "Inbox is empty."
    else:
        # Scan each message for injection signals before passing content to the agent.
        sources = []
        lines = [f"Recent inbox ({len(emails)} messages):"]
        for msg in emails:
            from_list = msg.get("from") or [{}]
            sender = from_list[0].get("email", "unknown")
            subject = msg.get("subject", "(no subject)")
            preview = (msg.get("preview") or "")[:_PER_EMAIL_PREVIEW_CAP]
            lines.append(f"- [{msg['id']}] From: {sender} | {subject} | {preview}")
            sources.append({
                "type": "email",
                "id": msg["id"],
                "content": f"{subject}\n{preview}",
            })

        prov = prov_mod.scan_sources(sources)
        if prov["suspicious"]:
            lines.insert(1, "[WARNING] Injection signals detected in inbox content — details in audit ledger.")

        result = "\n".join(lines)
        if len(result) > _INBOX_CONTENT_CAP:
            result = result[:_INBOX_CONTENT_CAP] + "\n[... truncated]"

    ledger.append(
        "oracle.agent", "read_inbox", f"limit={limit}",
        "allow", "executed", result[:300],
        risk.category("read_inbox"), risk.score("read_inbox", {}, "allow"),
        provenance=prov,
    )
    return result
