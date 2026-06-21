"""
Home Assistant connector - the first real-world capability behind the gate.

Two modes, chosen automatically:
- LIVE  - if ORA_HA_URL and ORA_HA_TOKEN are set, calls a real instance via
          REST and maintains a WebSocket listener for live state_changed events.
- MOCK  - otherwise, an in-memory home with a handful of devices, so discovery
          and control work end to end for demos and tests.

Important: this module performs the *effect*. It does NOT decide whether the
effect is allowed - that is the consent gate's job.
"""
import os
import json
import asyncio
import urllib.request
import urllib.error

CONTROLLABLE = {
    "light", "switch", "fan", "lock", "cover", "climate",
    "media_player", "input_boolean", "scene", "script",
}

_GENERIC = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}
_BY_DOMAIN = {
    "lock":    {"lock": "lock", "unlock": "unlock",
                "on": "lock", "off": "unlock"},
    "cover":   {"open": "open_cover", "close": "close_cover",
                "on": "open_cover", "off": "close_cover"},
    "climate": {"set_temperature": "set_temperature",
                "heat": "set_hvac_mode", "cool": "set_hvac_mode",
                "auto": "set_hvac_mode", "off": "turn_off"},
}

_RESULT_STATE = {
    "turn_on": "on", "turn_off": "off",
    "lock": "locked", "unlock": "unlocked",
    "open_cover": "open", "close_cover": "closed",
    "set_temperature": "updated",
}

# Which HA state attributes to surface per domain.
_ATTRS_BY_DOMAIN: dict[str, list[str]] = {
    "light":        ["brightness", "color_temp", "rgb_color"],
    "climate":      ["current_temperature", "temperature", "hvac_mode",
                     "min_temp", "max_temp", "unit_of_measurement"],
    "media_player": ["media_title", "media_artist", "volume_level", "is_volume_muted"],
    "cover":        ["current_position"],
    "fan":          ["percentage"],
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


def _extract_attrs(raw: dict, domain: str) -> dict:
    """Pull only the attributes worth surfacing for this domain."""
    keys = _ATTRS_BY_DOMAIN.get(domain, [])
    return {k: raw[k] for k in keys if k in raw}


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
        domain = _domain(eid)
        if domain in CONTROLLABLE:
            raw_attrs = s.get("attributes", {})
            out.append({
                "entity_id": eid,
                "name": raw_attrs.get("friendly_name", eid),
                "state": s.get("state", "unknown"),
                "domain": domain,
                "attributes": _extract_attrs(raw_attrs, domain),
            })
    return out


def _live_control(entity: dict, service: str, extra: dict | None = None) -> str:
    domain = entity["domain"]
    payload: dict = {"entity_id": entity["entity_id"]}
    if extra:
        payload.update(extra)
    _ha_request("POST", f"/api/services/{domain}/{service}", payload)
    new_state = _RESULT_STATE.get(service, service)
    return f"{entity['name']} is now {new_state}."


# ----------------------------------------------------------------- mock backend
class _MockHome:
    """In-memory house so Ora is demoable and testable without hardware."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.devices = {
            "light.living_room":   {"name": "Living Room Lamp",  "state": "off",    "attrs": {"brightness": 200}},
            "light.bedroom":       {"name": "Bedroom Light",      "state": "off",    "attrs": {"brightness": 128}},
            "switch.coffee_maker": {"name": "Coffee Maker",       "state": "off",    "attrs": {}},
            "fan.office":          {"name": "Office Fan",          "state": "off",    "attrs": {"percentage": 50}},
            "lock.front_door":     {"name": "Front Door",          "state": "locked", "attrs": {}},
            "cover.garage":        {"name": "Garage Door",         "state": "closed", "attrs": {"current_position": 0}},
            "climate.thermostat":  {"name": "Thermostat",          "state": "heat",   "attrs": {
                "current_temperature": 68, "temperature": 70,
                "hvac_mode": "heat", "min_temp": 60, "max_temp": 85,
                "unit_of_measurement": "°F",
            }},
        }

    def list(self) -> list[dict]:
        return [
            {
                "entity_id": eid,
                "name": d["name"],
                "state": d["state"],
                "domain": _domain(eid),
                "attributes": dict(d["attrs"]),
            }
            for eid, d in self.devices.items()
        ]

    def control(self, entity: dict, service: str, extra: dict | None = None) -> str:
        eid = entity["entity_id"]
        new_state = _RESULT_STATE.get(service)
        if service == "toggle":
            cur = self.devices[eid]["state"]
            new_state = "off" if cur == "on" else "on"
        if new_state is not None:
            self.devices[eid]["state"] = new_state
        if extra and service == "turn_on" and "brightness" in extra:
            self.devices[eid]["attrs"]["brightness"] = extra["brightness"]
        if extra and service == "set_temperature" and "temperature" in extra:
            self.devices[eid]["attrs"]["temperature"] = extra["temperature"]
        return f"{entity['name']} is now {self.devices[eid]['state']}."


_mock = _MockHome()


def reset() -> None:
    """Reset the mock home. Used by tests; no effect on a live instance."""
    _mock.reset()


# ----------------------------------------------------------------- WebSocket listener
_ws_task: "asyncio.Task | None" = None
_ws_callback = None   # async callable(entity_id, state, attrs, domain, name)


async def _ha_ws_loop() -> None:
    """Background task: connect to HA WebSocket and forward state_changed events."""
    base = _url()
    ws_url = (
        base.replace("https://", "wss://").replace("http://", "ws://")
        + "/api/websocket"
    )

    while True:
        try:
            import websockets  # provided by uvicorn[standard]
            async with websockets.connect(ws_url) as ws:
                # 1. auth_required
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                if json.loads(raw).get("type") != "auth_required":
                    await asyncio.sleep(10)
                    continue

                # 2. authenticate
                await ws.send(json.dumps({"type": "auth", "access_token": _token()}))
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                if json.loads(raw).get("type") != "auth_ok":
                    await asyncio.sleep(30)
                    continue

                # 3. subscribe to state_changed
                await ws.send(json.dumps({
                    "id": 1,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }))

                # 4. process events until disconnect
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    if msg.get("type") != "event":
                        continue
                    data = msg.get("event", {}).get("data", {})
                    eid = data.get("entity_id", "")
                    ns = data.get("new_state") or {}
                    if not ns or _domain(eid) not in CONTROLLABLE:
                        continue
                    if _ws_callback is None:
                        continue
                    domain = _domain(eid)
                    raw_attrs = ns.get("attributes", {})
                    await _ws_callback(
                        eid,
                        ns.get("state", "unknown"),
                        _extract_attrs(raw_attrs, domain),
                        domain,
                        raw_attrs.get("friendly_name", eid),
                    )

        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(5)  # reconnect after brief pause


def start_ws_listener(callback) -> bool:
    """Start the HA WebSocket listener. Returns True if HA is configured."""
    global _ws_task, _ws_callback
    if not configured():
        return False
    _ws_callback = callback
    _ws_task = asyncio.create_task(_ha_ws_loop())
    return True


def stop_ws_listener() -> None:
    global _ws_task
    if _ws_task:
        _ws_task.cancel()
        _ws_task = None


# ----------------------------------------------------------------- public API
def list_devices() -> list[dict]:
    """Controllable devices, from the live instance if set, else the mock."""
    if configured():
        try:
            return _live_list()
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            return [{"entity_id": "error",
                     "name": f"Home Assistant unreachable: {e}",
                     "state": "error", "domain": "error", "attributes": {}}]
    return _mock.list()


def control(device: str, action: str, extra: dict | None = None) -> str:
    """Perform a device action with optional extra service params (brightness, temperature).

    Never raises into the gate's execute path.
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
            return prefix + _live_control(entity, service, extra)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            return f"Couldn't reach the {entity['name']}: {e}"
    return prefix + _mock.control(entity, service, extra)
