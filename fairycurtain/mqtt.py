"""
MQTT-koppeling met Home Assistant (discovery).

In HA verschijnt:
  * light.led_gordijn   – aan/uit, RGB, helderheid, effectenlijst (presets)
  * plus command-topics voor tekst en afbeeldingen (voor automatiseringen):

    <base>/cmd/text     payload: "Hallo"  of JSON {"text":"Hallo","color":"#ff0000","speed":12}
    <base>/cmd/image    payload: base64 van een PNG/JPG/GIF, of JSON {"url":"http://…/plaatje.png"}
    <base>/cmd/frame    payload: JSON {"hex":[...]} zoals /api/frame
    <base>/cmd/raw      payload: hex-string
    <base>/cmd/stop     stopt de animatie

  Status: <base>/state (JSON), <base>/availability (online/offline)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

from . import protocol as P
from . import render as R
from .device import FairyCurtain

log = logging.getLogger("fairycurtain.mqtt")


class MqttBridge:
    def __init__(self, curtain: FairyCurtain, cfg: dict, render_cfg: dict, nowplaying=None) -> None:
        self.nowplaying = nowplaying
        self.c = curtain
        self.cfg = cfg
        self.render_cfg = render_cfg
        self.base = cfg.get("base_topic", "fairycurtain")
        self.disc = cfg.get("discovery_prefix", "homeassistant")
        self.uid = "fairycurtain_" + (self.base.replace("/", "_"))
        self._client = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.c.on_state(self._on_state)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        import aiomqtt
        will = aiomqtt.Will(f"{self.base}/availability", "offline", retain=True)
        while True:
            try:
                async with aiomqtt.Client(self.cfg["host"], port=int(self.cfg.get("port", 1883)),
                                          username=self.cfg.get("username"), password=self.cfg.get("password"),
                                          will=will, identifier=self.uid) as client:
                    self._client = client
                    await self._announce()
                    await client.publish(f"{self.base}/availability", "online", retain=True)
                    await self._publish_state()
                    await client.subscribe(f"{self.base}/light/set")
                    await client.subscribe(f"{self.base}/cmd/#")
                    np_topic = (self.nowplaying.cfg.get("topic") if self.nowplaying else None)
                    if np_topic:
                        await client.subscribe(np_topic)
                    log.info("MQTT verbonden met %s", self.cfg["host"])
                    async for msg in client.messages:
                        try:
                            await self._handle(str(msg.topic), msg.payload)
                        except Exception as e:
                            log.warning("MQTT-commando mislukt (%s): %s", msg.topic, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._client = None
                log.warning("MQTT-verbinding weg: %s (opnieuw over 10s)", e)
                await asyncio.sleep(10)

    # ---------------------------------------------------------- discovery
    async def _announce(self) -> None:
        dev = {"identifiers": [self.uid], "name": self.cfg.get("device_name", "LED-gordijn"),
               "manufacturer": "Hello Fairy", "model": "BLE LED curtain via FairyCurtain"}
        effects = [P.PRESETS.get(n, f"Preset {n}") for n in range(1, 59)]
        light = {
            "name": None, "unique_id": self.uid + "_light", "schema": "json",
            "command_topic": f"{self.base}/light/set", "state_topic": f"{self.base}/light/state",
            "availability_topic": f"{self.base}/availability",
            "brightness": True, "supported_color_modes": ["rgb"], "effect": True, "effect_list": effects,
            "device": dev, "icon": "mdi:curtains",
        }
        await self._client.publish(f"{self.disc}/light/{self.uid}/config", json.dumps(light), retain=True)
        text = {"name": "Tekst", "unique_id": self.uid + "_text", "command_topic": f"{self.base}/cmd/text",
                "state_topic": f"{self.base}/text/state", "availability_topic": f"{self.base}/availability",
                "device": dev, "icon": "mdi:format-text", "max": 100}
        await self._client.publish(f"{self.disc}/text/{self.uid}_text/config", json.dumps(text), retain=True)
        stop = {"name": "Stop animatie", "unique_id": self.uid + "_stop", "command_topic": f"{self.base}/cmd/stop",
                "availability_topic": f"{self.base}/availability", "device": dev, "icon": "mdi:stop"}
        await self._client.publish(f"{self.disc}/button/{self.uid}_stop/config", json.dumps(stop), retain=True)

    # ------------------------------------------------------------- state
    def _on_state(self, state: dict) -> None:
        if self._loop and self._client:
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._publish_state()))

    async def _publish_state(self) -> None:
        if not self._client:
            return
        s = self.c.status()
        r, g, b = s.get("rgb") or [255, 255, 255]
        light = {"state": "ON" if s.get("power") else "OFF",
                 "brightness": int((s.get("brightness") or 100) * 255 / 100),
                 "color_mode": "rgb", "color": {"r": r, "g": g, "b": b}}
        if s.get("mode") == "preset" and s.get("preset"):
            light["effect"] = P.PRESETS.get(s["preset"], f"Preset {s['preset']}")
        try:
            await self._client.publish(f"{self.base}/light/state", json.dumps(light))
            await self._client.publish(f"{self.base}/state", json.dumps(s, default=str))
        except Exception as e:
            log.debug("publish: %s", e)

    # ---------------------------------------------------------- commands
    async def _handle(self, topic: str, payload: bytes) -> None:
        text = payload.decode(errors="replace").strip()
        c = self.c
        if topic.endswith("/light/set"):
            d = json.loads(text or "{}")
            if d.get("state") == "OFF":
                await c.set_master(False)
                return
            if not c.master_on:
                await c.set_master(True)
            bri = d.get("brightness")
            bri_pct = int(round(bri * 100 / 255)) if bri is not None else None
            if d.get("effect"):
                num = next((n for n in range(1, 59) if P.PRESETS.get(n, f"Preset {n}") == d["effect"]), None)
                if num:
                    await c.set_preset(num, bri_pct)
                    return
            if d.get("color"):
                col = d["color"]
                await c.set_rgb(int(col.get("r", 0)), int(col.get("g", 0)), int(col.get("b", 0)), bri_pct)
            elif bri_pct is not None:
                await c.set_brightness(bri_pct)
            else:
                await c.set_power(True)
            return

        if self.nowplaying and topic == self.nowplaying.cfg.get("topic"):
            await self.nowplaying.handle_mqtt(text)
            return
        cmd = topic.rsplit("/", 1)[-1]
        w, h = c.layout.width, c.layout.height
        bright = float(self.render_cfg.get("brightness", 0.5))
        if cmd == "stop":
            await c.stop_animation()
        elif cmd == "text":
            d = json.loads(text) if text.startswith("{") else {"text": text}
            frames = R.text_to_pages(d.get("text", ""), w, h, color=R.hex_to_rgb(d.get("color", "#ffffff")),
                                     page_seconds=float(d.get("page_seconds", 4)),
                                     font_path=self.render_cfg.get("font_path"))
            if d.get("movement"):
                await c.set_movement(d["movement"], apply=False)
            await c.play(frames, loop=d.get("loop", True), name="text")
            await self._client.publish(f"{self.base}/text/state", d.get("text", ""))
        elif cmd == "image":
            d = json.loads(text) if text.startswith("{") else {"data": text}
            if d.get("url"):
                import aiohttp
                async with aiohttp.ClientSession() as s, s.get(d["url"]) as r:
                    data = await r.read()
            else:
                data = base64.b64decode(d["data"].split(",", 1)[-1])
            kw = dict(fit=d.get("fit", "cover"), brightness=float(d.get("brightness", bright)))
            img = R.bytes_to_image(data)
            if getattr(img, "is_animated", False):
                await c.play(R.gif_bytes_to_frames(data, w, h, **kw), loop=d.get("loop", True), name="image")
            else:
                await c.show_grid(R.image_to_grid(img, w, h, **kw))
        elif cmd == "frame":
            d = json.loads(text)
            hexes = d["hex"]
            grid = [[R.hex_to_rgb(hexes[y * w + x]) for x in range(w)] for y in range(h)]
            await c.show_grid(grid)
        elif cmd == "movement":
            d = json.loads(text) if text.startswith("{") else {"direction": text}
            await c.set_movement(d.get("direction", "still"), d.get("brightness"), d.get("speed"))
        elif cmd == "raw":
            await c.send_raw(bytes.fromhex(text.replace(" ", "")))
