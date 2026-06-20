"""
Background observability: scheduled chain integrity self-verification.

Runs ledger.integrity() every ORA_VERIFY_INTERVAL_MINUTES (default 60).
Stores the last result for /health and /monitor/status.
Logs CRITICAL if the chain is ever found broken — a governed system that
doesn't tell you when its governance is broken isn't governed.
"""
import asyncio
import logging
import os
import time

import ledger

logger = logging.getLogger(__name__)

VERIFY_INTERVAL = int(os.getenv("ORA_VERIFY_INTERVAL_MINUTES", "60")) * 60

_state: dict = {}


def last_result() -> dict | None:
    """Return last integrity result + metadata, or None if not yet checked."""
    return dict(_state) if _state else None


async def _run_check() -> None:
    global _state
    try:
        result = ledger.integrity()
        _state = {
            "ok": result["valid"],
            "checked": result["checked"],
            "broken_at": result["broken_at"],
            "reason": result["reason"],
            "checked_at": time.time(),
        }
        if not result["valid"]:
            logger.critical(
                "CHAIN INTEGRITY FAILURE — %s (broken at entry #%s). "
                "Check oracle.db and oracle_keys/ immediately.",
                result["reason"],
                result["broken_at"],
            )
        else:
            logger.info("Self-verify OK: %d entries, chain intact.", result["checked"])
    except Exception as exc:
        logger.error("Self-verify error: %s", exc)


async def verify_loop() -> None:
    """Infinite loop: initial 30s delay (let server finish startup), then every VERIFY_INTERVAL."""
    await asyncio.sleep(30)
    while True:
        await _run_check()
        await asyncio.sleep(VERIFY_INTERVAL)
