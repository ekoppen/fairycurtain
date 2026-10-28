"""Start: python -m fairycurtain  (or: fairycurtain)"""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from .api import create_app
from .config import load_config
from .device import FairyCurtain
from .layout import Layout
from .transport import make_transport


def build(cfg: dict):
    dev = cfg["device"]
    curtain = FairyCurtain(
        make_transport(dev), Layout.from_dict(cfg["layout"]),
        pixel_encoder=dev.get("pixel_encoder", "diy"),
        send_init=bool(dev.get("send_init", True)),
        packet_delay=float(dev.get("packet_delay", 0.03)),
        max_fps=float(dev.get("max_fps", 0.4)),
        keepalive_interval=float(dev.get("keepalive_interval", 30)),
    )
    app = create_app(curtain, cfg)
    from .nowplaying import NowPlaying
    nowplaying = NowPlaying(curtain, cfg.get("nowplaying", {}), cfg.get("render", {}))
    app.state.nowplaying = nowplaying
    mqtt = None
    if cfg.get("mqtt", {}).get("enabled"):
        from .mqtt import MqttBridge
        mqtt = MqttBridge(curtain, cfg["mqtt"], cfg.get("render", {}), nowplaying=nowplaying)

    @app.on_event("startup")
    async def _startup():
        await curtain.start()
        await nowplaying.start()
        if mqtt:
            await mqtt.start()

    @app.on_event("shutdown")
    async def _shutdown():
        if mqtt:
            await mqtt.stop()
        await nowplaying.stop()
        await curtain.close()

    return app


def main() -> None:
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    logging.basicConfig(level=getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = build(cfg)
    api = cfg["api"]
    if api.get("host") not in ("127.0.0.1", "localhost", "::1") and not api.get("api_key"):
        logging.getLogger("fairycurtain").warning(
            "API is exposed on %s without api_key: anyone on the network can control the curtain", api.get("host"))
    uvicorn.run(app, host=api.get("host", "127.0.0.1"), port=int(api.get("port", 8321)), log_level="warning")


if __name__ == "__main__":
    main()
