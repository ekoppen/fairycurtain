from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS = {
    "device": {
        "transport": "bleak",          # bleak | esp32 | fake
        "address": None,               # BLE MAC; None = scannen op naam
        "name_prefix": "Hello Fairy-",
        "adapter": None,               # bijv. hci0
        "bridge_url": "http://fairy-bridge.local",  # alleen bij transport: esp32
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
        "brightness": 0.5,             # standaard-dimming voor afbeeldingen (LED's zijn fel)
        "gamma": 1.0,
        "font_path": None,
    },
    "api": {
        "host": "0.0.0.0", "port": 8321,
        "api_key": None,               # X-API-Key header; None = open
    },
    "mqtt": {
        "enabled": False,
        "host": "localhost", "port": 1883,
        "username": None, "password": None,
        "base_topic": "fairycurtain",
        "discovery_prefix": "homeassistant",
        "device_name": "LED-gordijn",
    },
    "nowplaying": {
        "enabled": False, "source": "http", "url": None, "interval": 5,
        "paths": {"title": "title", "artist": "artist", "image": "image"},
        "show": "cover", "text_seconds": 6, "fit": "cover", "movement": "still",
        "idle": "keep", "brightness": 1.0,
        "ha_url": None, "ha_token": None, "entity_id": None, "topic": None,
    },
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
    if p.exists():
        cfg = _merge(DEFAULTS, yaml.safe_load(p.read_text()) or {})
    # kleine env-overrides voor docker-gemak
    if os.environ.get("FAIRY_ADDRESS"):
        cfg["device"]["address"] = os.environ["FAIRY_ADDRESS"]
    if os.environ.get("FAIRY_TRANSPORT"):
        cfg["device"]["transport"] = os.environ["FAIRY_TRANSPORT"]
    if os.environ.get("FAIRY_API_KEY"):
        cfg["api"]["api_key"] = os.environ["FAIRY_API_KEY"]
    if os.environ.get("MQTT_HOST"):
        cfg["mqtt"]["host"] = os.environ["MQTT_HOST"]
        cfg["mqtt"]["enabled"] = True
    cfg["_path"] = str(p)
    return cfg


def save_layout(cfg: dict, layout: dict) -> None:
    """Schrijft alleen de layout-sectie terug naar config.yaml."""
    p = Path(cfg.get("_path", "config.yaml"))
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    data = data or {}
    data["layout"] = layout
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
