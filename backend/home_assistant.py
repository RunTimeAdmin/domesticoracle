"""
Home Assistant connector - the first real-world capability behind the gate.

This is what turns `control_device` from a simulation into an actual light
switching off in your house. It speaks the Home Assistant REST API with nothing
but the standard library (no new dependencies), in keeping with Ora's
local-first, minimal-footprint ethos.

Two modes, chosen automatically:
- LIVE  - if ORA_HA_URL and ORA_HA_TOKEN are set, calls a real instance.
- MOCK  - otherwise, an in-memory home with a handful of devices, so discovery
          and control work end to end for demos and tests without anyone's
          credentials or hardware.

Important: this module performs the *effect*. It does NOT decide whether the
effect is allowed - that is the consent gate's job. `control_device` is a
GUARDED tool, so every call here only happens after policy has allowed it (or
the owner has approved a hold).
"""
import os
import json
import urllib.request
import urllib.error

# Domains we treat as controllable smart-home devices. Everything else in Home
# Assistant (sensors, sun position, etc.) is ignored for control purposes.
CONTROLLABLE = {
    "light", "switch", "fan", "lock", "cover", "climate",
    "media_player", "input_boolean", "scene", "script",
}

# How a plain verb maps to a Home Assistant service, per domain. The gate has
# already decided this is allowed; here we just translate intent into a service.
_GENERIC = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}
_BY_DOMAIN = {
    "lock":  {"lock": "lock", "unlock": "unlock",
              "on": "lock", "off": "unlock"},
    "cover": {"open": "open_cover", "close": "close_cover",
              "on": "open_cover", "off": "close_cover"},
}

# Resulting state shown back to the user after a service call succeeds.
_RESULT_STATE = {
    "turn_on": "on", "turn_off": "off", "lock": "locked", "unlock": "unlocked",
    "open_cover": "open", "close_cover": "closed",
}


def _url() -> str:
    return (os.getenv("ORA_HA_URL") or "").rstrip("/")


def _token() -> str:
    return os.getenv("ORA_HA_TOKEN") or ""


def configured() -> bool:
    """True when a real Home Assistant instance is wired up."""
    return bool(_url() and _token())


# ----------------------------------------------------------------- helpers
def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def _service_for(domain: str, action: str) -> str | None:
    a = (action or "").lower().strip()
    if domain in _BY_DOMAIN and a in _BY_DOMAIN[domain]:
        return _BY_DOMAIN[domain][a]
    return _GENERIC.get(a)


def _match_entity(devices: list[dict], device: str) -> dict | None:
    """Resolve a spoken device name to a single entity in one pass.

    Priority: exact entity_id > exact friendly name > unique substring match.
    """
    q = (device or "").strip().lower()
    if not q:
        return None
    exact_id = exact_name = None
    substring_hits: list[dict] = []
    for d in devices:
        eid = d["entity_id"].lower()
        name = d["name"].lower()
        if eid == q:
            exact_id = d
            break
        if name == q:
            exact_name = d
        elif q in name or q in eid:
            substring_hits.append(d)
    if exact_id:
        return exact_id
    if exact_name:
        return exact_name
    return substring_hits[0] if len(substring_hits) == 1 else None


# ----------------------------------------------------------------- live backend
def _ha_request(method: str, path: str, payload: dict | None = None) -> object:
    req = urllib.request.Request(
        _url() + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else None


def _live_list() -> list[dict]:
    states = _ha_request("GET", "/api/states") or []
    out = []
    for s in states:
        eid = s.get("entity_id", "")
        if _domain(eid) in CONTROLLABLE:
            out.append({
                "entity_id": eid,
                "name": s.get("attributes", {}).get("friendly_name", eid),
                "state": s.get("state", "unknown"),
                "domain": _domain(eid),
            })
    return out


def _live_control(entity: dict, service: str) -> str:
    domain = entity["domain"]
    _ha_request("POST", f"/api/services/{domain}/{service}",
                {"entity_id": entity["entity_id"]})
    new_state = _RESULT_STATE.get(service, service)
    return f"{entity['name']} is now {new_state}."


# ----------------------------------------------------------------- mock backend
class _MockHome:
    """In-memory house so Ora is demoable and testable without hardware."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.devices = {
            "light.living_room":   {"name": "Living Room Lamp", "state": "off"},
            "light.bedroom":       {"name": "Bedroom Light",    "state": "off"},
            "switch.coffee_maker": {"name": "Coffee Maker",     "state": "off"},
            "fan.office":          {"name": "Office Fan",       "state": "off"},
            "lock.front_door":     {"name": "Front Door",   "state": "locked"},
            "cover.garage":        {"name": "Garage Door",  "state": "closed"},
            "climate.thermostat":  {"name": "Thermostat",     "state": "heat"},
        }

    def list(self) -> list[dict]:
        return [
            {"entity_id": eid, "name": d["name"], "state": d["state"],
             "domain": _domain(eid)}
            for eid, d in self.devices.items()
        ]

    def control(self, entity: dict, service: str) -> str:
        eid = entity["entity_id"]
        new_state = _RESULT_STATE.get(service)
        if service == "toggle":
            cur = self.devices[eid]["state"]
            new_state = "off" if cur == "on" else "on"
        if new_state is not None:
            self.devices[eid]["state"] = new_state
        return f"{entity['name']} is now {self.devices[eid]['state']}."


_mock = _MockHome()


def reset() -> None:
    """Reset the mock home. Used by tests; no effect on a live instance."""
    _mock.reset()


# ----------------------------------------------------------------- public API
def list_devices() -> list[dict]:
    """Controllable devices, from the live instance if set, else the mock."""
    if configured():
        try:
            return _live_list()
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            return [{"entity_id": "error",
                     "name": f"Home Assistant unreachable: {e}",
                     "state": "error", "domain": "error"}]
    return _mock.list()


def control(device: str, action: str) -> str:
    """Perform a device action. Returns a result or a clear, non-throwing error.

    Never raises into the gate's execute path: an unknown device or verb comes
    back as a friendly message so Ora can relay it, not crash the agent loop.
    """
    devices = list_devices()
    entity = _match_entity(devices, device)
    if not entity:
        known = ", ".join(sorted(d["name"] for d in devices)) or "none"
        return (f"I couldn't find a device called '{device}'. "
                f"Devices I can see: {known}.")

    service = _service_for(entity["domain"], action)
    if not service:
        return (f"I don't know how to '{action}' the {entity['name']}. "
                f"Try on, off, toggle, lock/unlock, or open/close.")

    prefix = "" if configured() else "[MOCK] "
    if configured():
        try:
            return prefix + _live_control(entity, service)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            return f"Couldn't reach the {entity['name']}: {e}"
    return prefix + _mock.control(entity, service)
