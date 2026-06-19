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

DeerFlowClient.stream() is a SYNC generator; we iterate it with a plain for loop
inside this async generator so FastAPI's StreamingResponse can await each yield.
"""
import re
from typing import AsyncIterator, Iterator, Any


async def deerflow_to_sse(events: Iterator) -> AsyncIterator[dict]:
    """Yield Ora-format SSE dicts from a DeerFlow sync event stream."""
    for event in events:
        event_type = getattr(event, "type", None)
        if event_type is None and isinstance(event, dict):
            event_type = event.get("type", "")

        data = getattr(event, "data", None)
        if data is None and isinstance(event, dict):
            data = event.get("data")

        if event_type == "messages-tuple" and isinstance(data, dict):
            msg_type = data.get("type", "")

            if msg_type == "ai":
                content = data.get("content", "")
                if isinstance(content, str) and content:
                    yield {"text": content}
                elif isinstance(content, list):
                    # Anthropic content-block format: [{"type": "text", "text": "..."}]
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                yield {"text": text}

            elif msg_type == "tool":
                content = data.get("content", "")
                if not isinstance(content, str):
                    content = str(content)
                if "Held for owner approval" in content:
                    m = re.search(r"approval #(apr_[0-9a-f]+)", content)
                    approval_id = m.group(1) if m else None
                    tool_name = data.get("name") or "unknown"
                    yield {
                        "approval": {
                            "id": approval_id,
                            "action": tool_name,
                            "summary": content[:300],
                        }
                    }

        elif event_type == "end":
            return
