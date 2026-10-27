"""
Schema voor config.yaml: wordt gebruikt voor
  * validatie op de server (PUT /api/config)
  * het automatisch opbouwen van de config-editor in de browser (GET /api/config/schema)

Elk veld: type, label (NL), help, en optioneel choices/min/max/secret/restart.
`restart: True` betekent dat de wijziging pas na een herstart van de service werkt.
"""
from __future__ import annotations

from typing import Any

SCHEMA: dict[str, dict] = {
    "device": {
        "label": "Gordijn & verbinding",
        "fields": {
            "transport": {"type": "choice", "choices": ["esp32", "bleak", "fake"], "label": "Transport",
                          "help": "esp32 = via ESP32-bridge (aanbevolen), bleak = Bluetooth-adapter op deze machine, fake = zonder hardware", "restart": True},
            "bridge_url": {"type": "url", "label": "ESP32-bridge URL", "help": "bv. http://192.168.178.145", "restart": True},
            "address": {"type": "mac", "label": "BLE-adres van het gordijn", "help": "Leeg = scannen op naam", "optional": True, "restart": True},
            "name_prefix": {"type": "str", "label": "Naamprefix bij scannen", "restart": True},
            "adapter": {"type": "str", "label": "Bluetooth-adapter (bleak)", "help": "bv. hci0", "optional": True, "restart": True},
            "pixel_encoder": {"type": "choice", "choices": ["diy", "legacy_indexed_da", "legacy_plain_rgb"], "label": "Pixel-encoder", "help": "diy is het bevestigde protocol"},
            "send_init": {"type": "bool", "label": "Init-reeks sturen na verbinden"},
            "packet_delay": {"type": "float", "min": 0, "max": 2, "label": "Pauze tussen pakketten (s)"},
            "max_fps": {"type": "float", "min": 0.05, "max": 30, "label": "Max. frames/s voor animaties", "help": "Een DIY-upload kost ~2,5 s; 0.4 is realistisch"},
            "keepalive_interval": {"type": "float", "min": 0, "max": 600, "label": "Keepalive-interval (s)", "help": "0 = uit", "restart": True},
        },
    },
    "layout": {
        "label": "Raster-layout",
        "fields": {
            "width": {"type": "int", "min": 1, "max": 256, "label": "Breedte (kolommen)"},
            "height": {"type": "int", "min": 1, "max": 256, "label": "Hoogte (rijen)"},
            "order": {"type": "choice", "choices": ["column", "row"], "label": "Volgorde", "help": "column = slierten van boven naar beneden"},
            "serpentine": {"type": "bool", "label": "Serpentine (om en om)"},
            "flip_x": {"type": "bool", "label": "Spiegel horizontaal"},
            "flip_y": {"type": "bool", "label": "Spiegel verticaal"},
        },
    },
    "render": {
        "label": "Weergave",
        "fields": {
            "brightness": {"type": "float", "min": 0, "max": 1, "label": "Standaard-dimming plaatjes (0–1)"},
            "gamma": {"type": "float", "min": 0.2, "max": 5, "label": "Gamma"},
            "font_path": {"type": "str", "label": "Lettertype (pad naar .ttf)", "optional": True},
        },
    },
    "api": {
        "label": "API",
        "fields": {
            "host": {"type": "str", "label": "Bind-adres", "restart": True},
            "port": {"type": "int", "min": 1, "max": 65535, "label": "Poort", "restart": True},
            "api_key": {"type": "str", "label": "API-key (X-API-Key)", "help": "Leeg = open", "optional": True, "secret": True, "restart": True},
        },
    },
    "mqtt": {
        "label": "MQTT / Home Assistant",
        "fields": {
            "enabled": {"type": "bool", "label": "Ingeschakeld", "restart": True},
            "host": {"type": "str", "label": "Broker", "restart": True},
            "port": {"type": "int", "min": 1, "max": 65535, "label": "Poort", "restart": True},
            "username": {"type": "str", "label": "Gebruiker", "optional": True, "restart": True},
            "password": {"type": "str", "label": "Wachtwoord", "optional": True, "secret": True, "restart": True},
            "base_topic": {"type": "str", "label": "Basis-topic", "restart": True},
            "discovery_prefix": {"type": "str", "label": "HA discovery-prefix", "restart": True},
            "device_name": {"type": "str", "label": "Naam in HA", "restart": True},
        },
    },
    "nowplaying": {
        "label": "Now playing",
        "fields": {
            "enabled": {"type": "bool", "label": "Volgen ingeschakeld"},
            "source": {"type": "choice", "choices": ["http", "homeassistant", "mqtt"], "label": "Bron", "restart": True},
            "url": {"type": "url", "label": "Status-URL (http)", "help": "bv. http://192.168.178.138:3000/api/status", "optional": True, "restart": True},
            "headers": {"type": "dict", "label": "Extra HTTP-headers", "help": "bv. Authorization: Bearer …", "optional": True, "secret": True, "restart": True},
            "interval": {"type": "float", "min": 1, "max": 3600, "label": "Poll-interval (s)", "restart": True},
            "paths": {"type": "dict", "label": "JSON-paden", "help": "title, artist, image, playing → bv. playback.currentTrack.title", "restart": True},
            "ha_url": {"type": "url", "label": "Home Assistant URL", "optional": True, "restart": True},
            "ha_token": {"type": "str", "label": "HA long-lived token", "optional": True, "secret": True, "restart": True},
            "entity_id": {"type": "str", "label": "media_player entity", "optional": True, "restart": True},
            "topic": {"type": "str", "label": "MQTT-topic (bron mqtt)", "optional": True, "restart": True},
            "show": {"type": "choice", "choices": ["cover", "text", "both"], "label": "Tonen"},
            "text_seconds": {"type": "float", "min": 0.5, "max": 60, "label": "Seconden per tekstpagina"},
            "fit": {"type": "choice", "choices": ["cover", "contain", "stretch"], "label": "Hoes passend maken"},
            "movement": {"type": "choice", "choices": ["still", "loop_left", "loop_right", "loop_up", "loop_down", "left", "right", "up", "down", "blink", "breathe"], "label": "Beweging"},
            "idle": {"type": "choice", "choices": ["keep", "off", "clear"], "label": "Bij pauze"},
            "brightness": {"type": "float", "min": 0, "max": 1, "label": "Dimming hoes (0–1)"},
        },
    },
    "log_level": {"label": "Logniveau", "type": "choice", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "restart": True},
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
        errors[key] = "verplicht"
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
                errors[key] = f"moet een van {spec['choices']} zijn"
            return val
        elif t == "url":
            val = str(val).strip()
            if not val.startswith(("http://", "https://")):
                errors[key] = "moet met http:// of https:// beginnen"
            return val
        elif t == "mac":
            import re
            val = str(val).strip()
            if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", val):
                errors[key] = "geen geldig MAC-adres (aa:bb:cc:dd:ee:ff)"
            return val
        elif t == "dict":
            if not isinstance(val, dict):
                errors[key] = "moet een sleutel/waarde-lijst zijn"
            return val
        else:
            val = str(val)
    except (TypeError, ValueError):
        errors[key] = f"ongeldige waarde voor type {t}"
        return val
    if "min" in spec and val < spec["min"]:
        errors[key] = f"minimaal {spec['min']}"
    if "max" in spec and val > spec["max"]:
        errors[key] = f"maximaal {spec['max']}"
    return val


def validate(cfg: dict) -> dict:
    """Valideert en normaliseert; gooit ConfigError met {veld: melding}."""
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
        errors["device.bridge_url"] = "verplicht bij transport esp32"
    np = out.get("nowplaying", {})
    if np.get("enabled"):
        if np.get("source") == "http" and not np.get("url"):
            errors["nowplaying.url"] = "verplicht bij bron http"
        if np.get("source") == "homeassistant" and not (np.get("ha_url") and np.get("ha_token") and np.get("entity_id")):
            errors["nowplaying.ha_url"] = "ha_url, ha_token en entity_id zijn verplicht bij bron homeassistant"
        if np.get("source") == "mqtt" and not np.get("topic"):
            errors["nowplaying.topic"] = "verplicht bij bron mqtt"
    if errors:
        raise ConfigError(errors)
    return out


def mask_secrets(cfg: dict) -> dict:
    """Secrets vervangen door '••••' (laatste 4 tekens blijven) voor weergave."""
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
    """Als de browser een gemaskeerde waarde terugstuurt, de echte waarde behouden."""
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
