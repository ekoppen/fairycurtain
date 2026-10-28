"""
"Now playing": follow a music source and show the album art (and/or
artist/title) on the curtain whenever the track changes.

Sources (config.yaml → nowplaying.source):

  http           polls a JSON endpoint (your own app, any music API, …)
  homeassistant  reads a media_player entity via the HA REST API
  mqtt           listens on a topic with JSON (e.g. from HA or Node-RED)

Fields are extracted from the JSON with simple paths ("a.b[0].c").
Example for your own app:

nowplaying:
  enabled: true
  source: http
  url: http://music.local/api/now-playing
  interval: 5                 # seconds
  paths:
    title:  track.title
    artist: track.artist
    image:  track.cover_url   # absolute URL, or relative to url
    playing: state            # optional; value "playing" / true
  show: cover                 # cover | text | both  (both = text first, then cover)
  text_seconds: 6
  fit: cover
  movement: still             # e.g. loop_left to make the cover move
  idle: keep                  # keep | off | clear  when nothing is playing
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin

from . import render as R
from .device import FairyCurtain
from .netsafe import fetch_image

log = logging.getLogger("fairycurtain.nowplaying")


def jpath(obj: Any, path: str) -> Any:
    """Extract 'a.b[0].c' from a JSON object; None if the path does not exist."""
    if not path:
        return None
    cur = obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        try:
            if part.startswith("["):
                cur = cur[int(part[1:-1])]
            else:
                cur = cur[part]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


class NowPlaying:
    def __init__(self, curtain: FairyCurtain, cfg: dict, render_cfg: dict) -> None:
        self.c = curtain
        self.cfg = cfg
        self.render_cfg = render_cfg
        self.enabled = bool(cfg.get("enabled", False))
        self.current: dict = {"title": None, "artist": None, "image": None, "playing": None}
        self._key: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._show_task: Optional[asyncio.Task] = None
        self._last_err: Optional[str] = None
        self._session = None

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None

    def status(self) -> dict:
        return {"enabled": self.enabled, "source": self.cfg.get("source"), "current": self.current}

    # --------------------------------------------------------------- bronnen
    async def _http(self):
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def _fetch_http(self) -> Optional[dict]:
        s = await self._http()
        headers = dict(self.cfg.get("headers") or {})
        async with s.get(self.cfg["url"], headers=headers) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        p = self.cfg.get("paths") or {}
        img = jpath(data, p.get("image", "image"))
        if isinstance(img, str) and not img.startswith(("http://", "https://", "data:")):
            img = urljoin(self.cfg["url"], img)
        return {
            "title": jpath(data, p.get("title", "title")),
            "artist": jpath(data, p.get("artist", "artist")),
            "image": img,
            "playing": jpath(data, p.get("playing")) if p.get("playing") else True,
        }

    async def _fetch_homeassistant(self) -> Optional[dict]:
        s = await self._http()
        base = self.cfg["ha_url"].rstrip("/")
        headers = {"Authorization": f"Bearer {self.cfg['ha_token']}"}
        async with s.get(f"{base}/api/states/{self.cfg['entity_id']}", headers=headers) as r:
            r.raise_for_status()
            st = await r.json()
        a = st.get("attributes", {})
        pic = a.get("entity_picture")
        if pic and pic.startswith("/"):
            pic = base + pic  # local HA proxy URL; token needed when fetching
        return {"title": a.get("media_title"), "artist": a.get("media_artist"),
                "image": pic, "playing": st.get("state") == "playing"}

    async def _fetch_image(self, url: str) -> bytes:
        import base64
        if url.startswith("data:"):
            data = base64.b64decode(url.split(",", 1)[-1])
            if len(data) > R.MAX_IMAGE_BYTES:
                raise ValueError("image too large")
            return data
        headers = {}
        trusted = False
        if self.cfg.get("source") == "homeassistant" and url.startswith(self.cfg["ha_url"].rstrip("/")):
            headers["Authorization"] = f"Bearer {self.cfg['ha_token']}"
            trusted = True  # the admin's own HA instance (may be on loopback); other URLs are guarded
        return await fetch_image(url, headers, trusted=trusted)

    # ---------------------------------------------------------------- loop
    async def _run(self) -> None:
        source = self.cfg.get("source", "http")
        if source == "mqtt":
            return  # the MQTT source is fed via handle_mqtt() from mqtt.py
        interval = float(self.cfg.get("interval", 5))
        log.info("Now playing: source %s, %s", source, self.cfg.get("url") or self.cfg.get("entity_id") or "", )
        while True:
            try:
                if self.enabled:
                    info = await (self._fetch_homeassistant() if source == "homeassistant" else self._fetch_http())
                    if info:
                        await self.update(info)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if msg != self._last_err:
                    log.warning("nowplaying: source %s not readable: %s", self.cfg.get("url") or source, msg)
                    self._last_err = msg
            await asyncio.sleep(interval)

    async def handle_mqtt(self, payload: str) -> None:
        """For source: mqtt — payload is JSON; same 'paths' as for http."""
        if not self.enabled:
            return
        try:
            data = json.loads(payload)
        except ValueError:
            return
        p = self.cfg.get("paths") or {}
        await self.update({
            "title": jpath(data, p.get("title", "title")),
            "artist": jpath(data, p.get("artist", "artist")),
            "image": jpath(data, p.get("image", "image")),
            "playing": jpath(data, p.get("playing")) if p.get("playing") else True,
        })

    async def update(self, info: dict) -> None:
        playing = info.get("playing")
        playing = playing in (True, "playing", "play", 1, "1", "true", "True") if playing is not None else True
        key = f"{info.get('artist')}|{info.get('title')}|{info.get('image')}|{playing}"
        if not self.c.master_on:
            self._key = None       # so it is shown again right after 'On'
            self.current = {**info, "playing": playing}
            return
        if key == self._key:
            return
        self._key = key
        self.current = {**info, "playing": playing}
        log.info("Now playing: %s – %s (%s)", info.get("artist"), info.get("title"), "playing" if playing else "idle")
        if not playing:
            idle = self.cfg.get("idle", "keep")
            if idle == "off":
                await self.c.set_power(False)
            elif idle == "clear":
                await self.c.show_solid(0, 0, 0)
            return
        if self._show_task and not self._show_task.done():
            self._show_task.cancel()
        self._show_task = asyncio.create_task(self._show(info))

    async def _show(self, info: dict) -> None:
        w, h = self.c.layout.width, self.c.layout.height
        show = self.cfg.get("show", "cover")
        label = "Now playing: " + " – ".join(x for x in (info.get("artist"), info.get("title")) if x)
        if self.cfg.get("movement"):
            await self.c.set_movement(self.cfg["movement"], apply=False)
        if show in ("text", "both"):
            text = " ".join(x for x in (info.get("artist"), info.get("title")) if x)
            frames = R.text_to_pages(text or "?", w, h, color=R.hex_to_rgb(self.cfg.get("text_color", "#ffffff")),
                                     page_seconds=float(self.cfg.get("text_seconds", 6)) / 1.0,
                                     font_path=self.render_cfg.get("font_path"))
            if show == "text":
                await self.c.play(frames, loop=True, name="nowplaying:text", source=("nowplaying", label))
                return
            await self.c.play(frames, loop=False, name="nowplaying:text", source=("nowplaying", label))
            # wait until the text animation has finished
            while self.c.state.get("animation"):
                await asyncio.sleep(0.5)
        if info.get("image"):
            try:
                data = await self._fetch_image(info["image"])
                grid = R.image_bytes_to_grid(data, w, h, fit=self.cfg.get("fit", "cover"),
                                             brightness=float(self.cfg.get("brightness", 1.0)))
                await self.c.show_grid(grid, source=("nowplaying", label))
            except Exception as e:
                log.warning("Fetching/showing cover failed: %s", e)
