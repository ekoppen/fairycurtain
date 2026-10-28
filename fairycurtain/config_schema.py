"""
Schema for config.yaml: used for
  * validation on the server (PUT /api/config)
  * automatically building the config editor in the browser (GET /api/config/schema)

Each field: type, label, help, and optionally choices/min/max/secret/restart.
`restart: True` means the change only takes effect after a service restart.
"""
from __future__ import annotations

from typing import Any

SCHEMA: dict[str, dict] = {
    "device": {
        "label": "Curtain & connection",
        "fields": {
            "transport": {"type": "choice", "choices": ["esp32", "bleak", "fake"], "label": "Transport",
                          "help": "esp32 = via ESP32 bridge (recommended), bleak = Bluetooth adapter on this machine, fake = no hardware", "restart": True},
            "bridge_url": {"type": "url", "label": "ESP32 bridge URL", "help": "e.g. http://192.168.1.60", "restart": True},
            "address": {"type": "mac", "label": "BLE address of the curtain", "help": "Empty = scan by name", "optional": True, "restart": True},
            "name_prefix": {"type": "str", "label": "Name prefix when scanning", "restart": True},
            "adapter": {"type": "str", "label": "Bluetooth adapter (bleak)", "help": "e.g. hci0", "optional": True, "restart": True},
            "pixel_encoder": {"type": "choice", "choices": ["diy", "legacy_indexed_da", "legacy_plain_rgb"], "label": "Pixel encoder", "help": "diy is the confirmed protocol"},
            "send_init": {"type": "bool", "label": "Send init sequence after connecting"},
            "packet_delay": {"type": "float", "min": 0, "max": 2, "label": "Delay between packets (s)"},
            "max_fps": {"type": "float", "min": 0.05, "max": 30, "label": "Max frames/s for animations", "help": "A DIY upload takes ~2.5 s; 0.4 is realistic"},
            "keepalive_interval": {"type": "float", "min": 0, "max": 600, "label": "Keepalive interval (s)", "help": "0 = off", "restart": True},
        },
    },
    "layout": {
        "label": "Grid layout",
        "fields": {
            "width": {"type": "int", "min": 1, "max": 256, "label": "Width (columns)"},
            "height": {"type": "int", "min": 1, "max": 256, "label": "Height (rows)"},
            "order": {"type": "choice", "choices": ["column", "row"], "label": "Order", "help": "column = strands top to bottom"},
            "serpentine": {"type": "bool", "label": "Serpentine (alternating)"},
            "flip_x": {"type": "bool", "label": "Flip horizontally"},
            "flip_y": {"type": "bool", "label": "Flip vertically"},
        },
    },
    "render": {
        "label": "Rendering",
        "fields": {
            "brightness": {"type": "float", "min": 0, "max": 1, "label": "Default image dimming (0–1)"},
            "gamma": {"type": "float", "min": 0.2, "max": 5, "label": "Gamma"},
            "font_path": {"type": "str", "label": "Font (path to .ttf)", "optional": True},
        },
    },
    "api": {
        "label": "API",
        "fields": {
            "host": {"type": "str", "label": "Bind address", "restart": True},
            "port": {"type": "int", "min": 1, "max": 65535, "label": "Port", "restart": True},
            "api_key": {"type": "str", "label": "API-key (X-API-Key)", "help": "Empty = open", "optional": True, "secret": True, "restart": True},
        },
    },
    "mqtt": {
        "label": "MQTT / Home Assistant",
        "fields": {
            "enabled": {"type": "bool", "label": "Enabled", "restart": True},
            "host": {"type": "str", "label": "Broker", "restart": True},
            "port": {"type": "int", "min": 1, "max": 65535, "label": "Port", "restart": True},
            "username": {"type": "str", "label": "Username", "optional": True, "restart": True},
            "password": {"type": "str", "label": "Password", "optional": True, "secret": True, "restart": True},
            "base_topic": {"type": "str", "label": "Base topic", "restart": True},
            "discovery_prefix": {"type": "str", "label": "HA discovery prefix", "restart": True},
            "device_name": {"type": "str", "label": "Name in HA", "restart": True},
        },
    },
    "nowplaying": {
        "label": "Now playing",
        "fields": {
            "enabled": {"type": "bool", "label": "Following enabled"},
            "source": {"type": "choice", "choices": ["http", "homeassistant", "mqtt"], "label": "Source", "restart": True},
            "url": {"type": "url", "label": "Status URL (http)", "help": "e.g. http://music.local:3000/api/status", "optional": True, "restart": True},
            "headers": {"type": "dict", "label": "Extra HTTP headers", "help": "e.g. Authorization: Bearer …", "optional": True, "secret": True, "restart": True},
            "interval": {"type": "float", "min": 1, "max": 3600, "label": "Poll interval (s)", "restart": True},
            "paths": {"type": "dict", "label": "JSON paths", "help": "title, artist, image, playing → e.g. playback.currentTrack.title", "restart": True},
            "ha_url": {"type": "url", "label": "Home Assistant URL", "optional": True, "restart": True},
            "ha_token": {"type": "str", "label": "HA long-lived token", "optional": True, "secret": True, "restart": True},
            "entity_id": {"type": "str", "label": "media_player entity", "optional": True, "restart": True},
            "topic": {"type": "str", "label": "MQTT topic (source mqtt)", "optional": True, "restart": True},
            "show": {"type": "choice", "choices": ["cover", "text", "both"], "label": "Show"},
            "text_seconds": {"type": "float", "min": 0.5, "max": 60, "label": "Seconds per text page"},
            "fit": {"type": "choice", "choices": ["cover", "contain", "stretch"], "label": "Fit cover"},
            "movement": {"type": "choice", "choices": ["still", "loop_left", "loop_right", "loop_up", "loop_down", "left", "right", "up", "down", "blink", "breathe"], "label": "Movement"},
            "idle": {"type": "choice", "choices": ["keep", "off", "clear"], "label": "When paused"},
            "brightness": {"type": "float", "min": 0, "max": 1, "label": "Cover dimming (0–1)"},
        },
    },
    "log_level": {"label": "Log level", "type": "choice", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "restart": True},
}


class ConfigError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _check(spec: dict, val: Any, key: str, errors: dict) -> Any:
    t = spec["type"]
    if val in (None, ""):
        if spec.get("optional") or t == "bool":
            return None if t != "bool" else False
        errors[key] = "required"
        return val
    try:
        if t == "bool":
            if isinstance(val, str):
                val = val.lower() in ("1", "true", "ja", "yes", "on")
            return bool(val)
        if t == "int":
            val = int(val)
        elif t == "float":
            val = float(val)
        elif t == "choice":
            if val not in spec["choices"]:
                errors[key] = f"must be one of {spec['choices']}"
            return val
        elif t == "url":
            val = str(val).strip()
            if not val.startswith(("http://", "https://")):
                errors[key] = "must start with http:// or https://"
            return val
        elif t == "mac":
            import re
            val = str(val).strip()
            if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", val):
                errors[key] = "not a valid MAC address (aa:bb:cc:dd:ee:ff)"
            return val
        elif t == "dict":
            if not isinstance(val, dict):
                errors[key] = "must be a key/value mapping"
            return val
        else:
            val = str(val)
    except (TypeError, ValueError):
        errors[key] = f"invalid value for type {t}"
        return val
    if "min" in spec and val < spec["min"]:
        errors[key] = f"minimaal {spec['min']}"
    if "max" in spec and val > spec["max"]:
        errors[key] = f"maximaal {spec['max']}"
    return val


def validate(cfg: dict) -> dict:
    """Validates and normalizes; raises ConfigError with {field: message}."""
    errors: dict[str, str] = {}
    out: dict = {}
    for section, sspec in SCHEMA.items():
        if "fields" not in sspec:
            out[section] = _check(sspec, cfg.get(section), section, errors)
            continue
        src = cfg.get(section) or {}
        out[section] = {}
        for fname, fspec in sspec["fields"].items():
            out[section][fname] = _check(fspec, src.get(fname), f"{section}.{fname}", errors)
    # kruisregels
    dev = out.get("device", {})
    if dev.get("transport") == "esp32" and not dev.get("bridge_url"):
        errors["device.bridge_url"] = "required for transport esp32"
    np = out.get("nowplaying", {})
    if np.get("enabled"):
        if np.get("source") == "http" and not np.get("url"):
            errors["nowplaying.url"] = "required for source http"
        if np.get("source") == "homeassistant" and not (np.get("ha_url") and np.get("ha_token") and np.get("entity_id")):
            errors["nowplaying.ha_url"] = "ha_url, ha_token and entity_id are required for source homeassistant"
        if np.get("source") == "mqtt" and not np.get("topic"):
            errors["nowplaying.topic"] = "required for source mqtt"
    if errors:
        raise ConfigError(errors)
    return out


def mask_secrets(cfg: dict) -> dict:
    """Replace secrets with '••••' (last 4 characters kept) for display."""
    import copy
    out = copy.deepcopy(cfg)
    for section, sspec in SCHEMA.items():
        for fname, fspec in sspec.get("fields", {}).items():
            if fspec.get("secret") and out.get(section, {}).get(fname):
                v = out[section][fname]
                if isinstance(v, dict):
                    out[section][fname] = {k: _mask(str(x)) for k, x in v.items()}
                else:
                    out[section][fname] = _mask(str(v))
    return out


def _mask(s: str) -> str:
    return "••••" + s[-4:] if len(s) > 8 else "••••"


def merge_secrets(new: dict, current: dict) -> dict:
    """If the browser sends a masked value back, keep the real value."""
    for section, sspec in SCHEMA.items():
        for fname, fspec in sspec.get("fields", {}).items():
            if not fspec.get("secret"):
                continue
            nv = (new.get(section) or {}).get(fname)
            cv = (current.get(section) or {}).get(fname)
            if isinstance(nv, dict) and isinstance(cv, dict):
                for k, x in list(nv.items()):
                    if isinstance(x, str) and x.startswith("••••") and k in cv:
                        nv[k] = cv[k]
            elif isinstance(nv, str) and nv.startswith("••••"):
                new.setdefault(section, {})[fname] = cv
    return new
