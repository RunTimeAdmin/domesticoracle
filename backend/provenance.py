"""
Prompt-injection provenance tagging.

Adapted from RunTimeAdmin/agent-flight-recorder mcp_security.py (MIT).

Every ledger entry that touches external content carries a provenance record:
where did the content come from and did any of it show injection signals?

Provenance is stored in args_json["_provenance"], which is covered by the
Ed25519 signature — it cannot be stripped or altered without breaking the chain.

Source type tokens mirror the agent-flight-recorder input_context taxonomy:
  user, ha_entity, web_fetch, web_search, mcp_tool, tool_result
"""
from __future__ import annotations

import re
import time

SCANNER_VERSION = 1
PROVENANCE_KEY  = "_provenance"

# Source-type tokens (matches AFR input_context taxonomy)
SOURCE_USER   = "user"
SOURCE_HA     = "ha_entity"
SOURCE_WEB    = "web_fetch"
SOURCE_SEARCH = "web_search"
SOURCE_MCP    = "mcp_tool"
SOURCE_TOOL   = "tool_result"


# ── Pattern registry ─────────────────────────────────────────────────────────
# (pattern_id, compiled_regex, human-readable description)
# Core patterns adapted from AFR mcp_security.validate_tool_description;
# extended with standard prompt-injection research patterns.

_RULES: list[tuple[str, re.Pattern, str]] = []


def _rule(pid: str, regex: str, desc: str) -> None:
    _RULES.append((pid, re.compile(regex, re.IGNORECASE | re.DOTALL), desc))


# Role / instruction override
_rule("role_override",
      r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
      "Override prior instructions")
_rule("role_override",
      r"disregard\s+(?:all\s+)?(?:previous|prior|above|the\s+above)",
      "Disregard prior instructions")
_rule("role_override",
      r"forget\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions?|context|conversation)",
      "Erase conversation context")
_rule("role_override",
      r"your\s+(?:new\s+)?(?:instructions?|task|role|purpose|directives?)\s+(?:is|are|now)\s*[:\-]",
      "Reassign agent role or task")
_rule("role_override",
      r"(?:act|behave)\s+as\s+(?:if\s+)?(?:you\s+(?:are|were)\s+)?(?:a\s+)?(?:different|new|unrestricted|jailbroken|uncensored)\b",
      "Change agent persona to bypass restrictions")

# Jailbreak
_rule("jailbreak",
      r"\bdan\b.{0,30}\bdo\s+anything\s+now\b",
      "DAN (Do Anything Now) pattern")
_rule("jailbreak",
      r"\bjailbreak\b",
      "Jailbreak keyword")
_rule("jailbreak",
      r"\bdeveloper\s+mode\b",
      "Developer / unrestricted mode claim")

# LLM prompt delimiters injected into external content
_rule("prompt_delimiter", r"<\|system\|>",       "LLM system-role delimiter")
_rule("prompt_delimiter", r"\[INST\]",            "Instruction wrapper token")
_rule("prompt_delimiter", r"<<SYS>>",             "System prompt wrapper token")
_rule("prompt_delimiter", r"\[SYSTEM\]",          "SYSTEM role injection token")
_rule("prompt_delimiter", r"###\s*System\s*:",    "Markdown system-section injection")
_rule("prompt_delimiter", r"<\|im_start\|>",      "ChatML role delimiter")

# Data exfiltration — adapted from AFR validate_tool_description
_rule("exfiltration",
      r"(?:send|transmit|forward|email|post|upload|exfil(?:trate)?)\b.{0,80}"
      r"\b(?:api[_\s]?key|secret|password|token|credential|private[_\s]?key)",
      "Exfiltrate credentials / API keys")
_rule("exfiltration",
      r"conversation\s+history.{0,60}(?:send|forward|external|outside|upload)",
      "Exfiltrate conversation history")
_rule("exfiltration",
      r"\bexfil\b",
      "Exfiltration keyword (AFR mcp_security)")

# Hidden-content tricks
_rule("hidden_content", r"<!--.{0,500}-->",
      "Hidden HTML comment")
_rule("hidden_content",
      r"\\u200[b-f]|\\u202[ef]|\\ufeff",
      "Unicode zero-width / RTL-override escape sequences")

# Literal hidden-unicode characters (not escape sequences)
_HIDDEN_UNICODE = (
    "​",  # zero-width space
    "‌",  # zero-width non-joiner
    "‍",  # zero-width joiner
    "‮",  # right-to-left override
    "﻿",  # byte-order mark
)

_EXCERPT_WINDOW = 80  # chars after match to include


def _excerpt(text: str, match: re.Match) -> str:
    start = max(0, match.start() - 20)
    end   = min(len(text), match.end() + _EXCERPT_WINDOW)
    raw   = text[start:end].replace("\n", " ").strip()
    return raw[:120]


# ── Core scanner ─────────────────────────────────────────────────────────────

def scan(text: str, source_id: str = "") -> dict:
    """Scan a single text payload for injection signals.

    Returns:
        {"suspicious": bool, "signals": [{"pattern_id", "description", "excerpt", "source_id"}]}
    """
    if not text:
        return {"suspicious": False, "signals": []}

    signals: list[dict] = []

    for pid, pattern, desc in _RULES:
        m = pattern.search(text)
        if m:
            signals.append({
                "pattern_id":  pid,
                "description": desc,
                "excerpt":     _excerpt(text, m),
                "source_id":   source_id,
            })

    if any(ch in text for ch in _HIDDEN_UNICODE):
        signals.append({
            "pattern_id":  "hidden_content",
            "description": "Hidden unicode chars (zero-width / RTL-override / BOM)",
            "excerpt":     "(non-printable chars present)",
            "source_id":   source_id,
        })

    # Length heuristic from AFR validate_tool_description — large payloads can
    # bury injected instructions past the visible window.
    if len(text) > 20_000:
        signals.append({
            "pattern_id":  "oversized_content",
            "description": f"Payload is {len(text):,} chars (>20 KB) — may hide instructions",
            "excerpt":     f"({len(text):,} chars total)",
            "source_id":   source_id,
        })

    return {"suspicious": bool(signals), "signals": signals}


def scan_sources(sources: list[dict]) -> dict:
    """Scan a list of content sources and build the _provenance record.

    Each source dict:
        {"type": str, "id": str, "content": str (optional)}

    Returns the _provenance dict to be stored inside args_json.
    """
    all_signals: list[dict] = []

    for src in sources:
        content = src.get("content", "")
        if content:
            result = scan(content, source_id=src.get("id") or src.get("type", ""))
            all_signals.extend(result["signals"])

    clean_sources = [
        {"type": s.get("type", ""), "id": s.get("id", "")}
        for s in sources
    ]

    return {
        "sources":         clean_sources,
        "signals":         all_signals,
        "suspicious":      bool(all_signals),
        "scanned_at":      time.time(),
        "scanner_version": SCANNER_VERSION,
    }


def patterns() -> list[dict]:
    """Return the full pattern registry for /provenance/patterns."""
    out: list[dict] = []
    for pid, _, desc in _RULES:
        out.append({"pattern_id": pid, "description": desc})
    out.append({
        "pattern_id":  "hidden_content",
        "description": "Hidden unicode chars (zero-width / RTL-override / BOM)",
    })
    out.append({
        "pattern_id":  "oversized_content",
        "description": "Payload >20 KB — may hide injected instructions",
    })
    return out
