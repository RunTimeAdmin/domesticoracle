"""
Stream adapter: DeerFlow StreamEvent → Ora SSE wire format.

Ora's frontend (stream.ts) parses three frame shapes:
  {"text": "..."}       — streaming text token
  {"approval": {...}}   — a guarded action was held, show approval card
  {"done": true}        — stream finished (emitted by main.py after the loop)

DeerFlow emits StreamEvent objects where .data is a dict:
  type="messages-tuple"  data={"type": "ai",   "content": <str|list>, "id": str}
  type="messages-tuple"  data={"type": "tool",  "content": str, "name": str, ...}
  type="values"          data={"messages": [...], ...}   — state snapshot (ignored)
  type="custom"          data=any                        — metadata (ignored)
  type="end"             data={"usage": {...}}           — signals completion

KEY DESIGN: DeerFlowClient.stream() is a SYNC generator. Iterating it on the
asyncio event loop thread blocks the loop for every token — while one chat
streams, all other requests (Trust Center, approvals, health) are starved.

Fix: the sync generator is drained on a worker thread and frames are handed
back through an asyncio.Queue. The event loop stays free for concurrent work.
The throughput ceiling is removed: multiple chats and Trust Center calls can
proceed in parallel.
"""
import asyncio
import re
from typing import AsyncIterator, Iterator


async def deerflow_to_sse(events: Iterator) -> AsyncIterator[dict]:
    """Bridge a SYNC DeerFlow generator to an async SSE stream.

    Pumps the synchronous iterator on a worker thread so blocking reads do
    not stall the event loop. Frames arrive through an asyncio.Queue bounded
    at 64 to provide light back-pressure without unbounded memory growth.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()
    _SENTINEL = object()

    def _pump() -> None:
        try:
            for event in events:
                for frame in _translate(event):
                    asyncio.run_coroutine_threadsafe(queue.put(frame), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(_SENTINEL), loop).result()

    pump_task = asyncio.create_task(asyncio.to_thread(_pump))

    while True:
        frame = await queue.get()
        if frame is _SENTINEL:
            break
        yield frame

    await pump_task  # surface any exception from the worker thread


def _translate(event) -> list[dict]:
    """Translate one DeerFlow event into zero or more SSE frame dicts.

    Extracted as a pure function so it is independently testable and has no
    side effects (all I/O happens in the caller's thread context).
    """
    event_type = getattr(event, "type", None)
    if event_type is None and isinstance(event, dict):
        event_type = event.get("type", "")

    data = getattr(event, "data", None)
    if data is None and isinstance(event, dict):
        data = event.get("data")

    if event_type == "end":
        return []

    frames: list[dict] = []

    if event_type == "messages-tuple" and isinstance(data, dict):
        msg_type = data.get("type", "")

        if msg_type == "ai":
            content = data.get("content", "")
            if isinstance(content, str) and content:
                frames.append({"text": content})
            elif isinstance(content, list):
                # Anthropic content-block format: [{"type": "text", "text": "..."}]
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            frames.append({"text": text})

        elif msg_type == "tool":
            content = data.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if "Held for owner approval" in content:
                m = re.search(r"approval #(apr_[0-9a-f]+)", content)
                frames.append({
                    "approval": {
                        "id": m.group(1) if m else None,
                        "action": data.get("name") or "unknown",
                        "summary": content[:300],
                    }
                })

    return frames
