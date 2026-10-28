from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS = {
    "device": {
        "transport": "bleak",          # bleak | esp32 | fake
        "address": None,               # BLE MAC; None = scan by name
        "name_prefix": "Hello Fairy-",
        "adapter": None,               # e.g. hci0
        "bridge_url": "http://fairy-bridge.local",  # only for transport: esp32
        "pixel_encoder": "diy",
        "send_init": True,
        "packet_delay": 0.03,
        "max_fps": 0.4,
        "keepalive_interval": 30,
    },
    "layout": {
        "width": 20, "height": 20, "order": "column",
        "serpentine": False, "flip_x": False, "flip_y": False,
    },
    "render": {
        "brightness": 0.5,             # default dimming for images (LEDs are bright)
        "gamma": 1.0,
        "font_path": None,
    },
    "api": {
        "host": "127.0.0.1", "port": 8321,   # loopback by default; set 0.0.0.0 (or FAIRY_HOST) to expose
        "api_key": None,               # X-API-Key header; None = open (no auth)
        "allowed_hosts": [],           # extra Host names accepted when api_key is unset (anti DNS rebinding)
    },
    "mqtt": {
        "enabled": False,
        "host": "localhost", "port": 1883,
        "username": None, "password": None,
        "base_topic": "fairycurtain",
        "discovery_prefix": "homeassistant",
        "device_name": "LED curtain",
    },
    "nowplaying": {
        "enabled": False, "source": "http", "url": None, "headers": {}, "interval": 5,
        "paths": {"title": "title", "artist": "artist", "image": "image", "playing": None},
        "show": "cover", "text_seconds": 6, "fit": "cover", "movement": "still",
        "idle": "keep", "brightness": 1.0,
        "ha_url": None, "ha_token": None, "entity_id": None, "topic": None,
    },
    "gallery": {"dir": None},          # where saved pictures live; None = "gallery/" next to config.yaml
    "log_level": "INFO",
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None = None) -> dict:
    path = path or os.environ.get("FAIRY_CONFIG", "config.yaml")
    cfg = DEFAULTS
    p = Path(path)
    file_cfg: dict = {}
    if p.exists():
        file_cfg = yaml.safe_load(p.read_text()) or {}
        cfg = _merge(DEFAULTS, file_cfg)
    # small env overrides for Docker convenience
    if os.environ.get("FAIRY_ADDRESS"):
        cfg["device"]["address"] = os.environ["FAIRY_ADDRESS"]
    if os.environ.get("FAIRY_TRANSPORT"):
        cfg["device"]["transport"] = os.environ["FAIRY_TRANSPORT"]
    if os.environ.get("FAIRY_HOST") and "host" not in (file_cfg.get("api") or {}):
        cfg["api"]["host"] = os.environ["FAIRY_HOST"]  # e.g. Docker image sets 0.0.0.0
    if os.environ.get("FAIRY_API_KEY"):
        cfg["api"]["api_key"] = os.environ["FAIRY_API_KEY"]
    if os.environ.get("MQTT_HOST"):
        cfg["mqtt"]["host"] = os.environ["MQTT_HOST"]
        cfg["mqtt"]["enabled"] = True
    cfg["_path"] = str(p)
    return cfg


def save_layout(cfg: dict, layout: dict) -> None:
    """Writes only the layout section back to config.yaml."""
    p = Path(cfg.get("_path", "config.yaml"))
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    data = data or {}
    data["layout"] = layout
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def save_config(cfg: dict, data: dict) -> None:
    """Writes the full (validated) config to config.yaml; keeps unknown keys."""
    p = Path(cfg.get("_path", "config.yaml"))
    existing = (yaml.safe_load(p.read_text()) if p.exists() else {}) or {}
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(existing.get(k), dict):
            existing[k] = {**existing[k], **v}
        else:
            existing[k] = v
    p.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True))
