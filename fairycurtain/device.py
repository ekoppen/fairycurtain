"""
FairyCurtain: a single object that manages the connection, the state and the
sending of commands/frames/animations. All API, MQTT and UI layers talk to it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, List, Optional

from . import protocol as P
from .layout import Grid, Layout
from .render import Frame, solid
from .transport import Transport

log = logging.getLogger("fairycurtain.device")
StateCb = Callable[[dict], None]


class FairyCurtain:
    def __init__(self, transport: Transport, layout: Layout, *,
                 pixel_encoder: str = "diy",
                 send_init: bool = True,
                 packet_delay: float = 0.03,
                 ack_timeout: float = 1.0,
                 reconnect_interval: float = 10.0,
                 max_fps: float = 8.0,
                 keepalive_interval: float = 30.0) -> None:
        self.transport = transport
        self.layout = layout
        self.pixel_encoder = pixel_encoder
        self.send_init = send_init
        self.packet_delay = packet_delay
        self.ack_timeout = ack_timeout
        self.reconnect_interval = reconnect_interval
        self.min_frame_time = 1.0 / max(0.1, max_fps)
        self.keepalive_interval = keepalive_interval

        self.state: dict = {
            "connected": False, "power": None, "mode": None,
            "rgb": [255, 255, 255], "brightness": 100,
            "preset": None, "animation": None, "last_error": None,
            "source": {"kind": "unknown", "label": "Onbekend", "since": None},
            "master": True,   # master switch: False = everything stays off, including now playing
            "last_notification": None, "info": None,
            "movement": {"direction": 0, "brightness": 100, "speed": 100},
        }
        self._keepalive_task: Optional[asyncio.Task] = None
        self.last_grid: Optional[Grid] = None
        self._lock = asyncio.Lock()
        self._ack = asyncio.Event()
        self._anim_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._listeners: List[StateCb] = []
        self._initialised = False
        transport.on_notify(self._on_notify)

    # ------------------------------------------------------------ state --

    def on_state(self, cb: StateCb) -> None:
        self._listeners.append(cb)

    def _emit(self) -> None:
        self.state["connected"] = self.transport.is_connected
        for cb in self._listeners:
            try:
                cb(self.status())
            except Exception as e:  # pragma: no cover
                log.debug("listener: %s", e)

    def set_source(self, kind: str, label: str) -> None:
        """What currently determines the display: manual | nowplaying | preset | color | off | remote."""
        self.state["source"] = {"kind": kind, "label": label, "since": time.time()}

    def status(self) -> dict:
        d = dict(self.state)
        d["connected"] = self.transport.is_connected
        d["transport"] = self.transport.info
        d["layout"] = self.layout.to_dict()
        d["pixel_encoder"] = self.pixel_encoder
        return d

    def _on_notify(self, data: bytes) -> None:
        self.state["last_notification"] = data.hex()
        if P.is_ack(data):
            self._ack.set()
            return
        info = P.parse_info(data)
        if info:
            self.state["info"] = info
            if info["columns"] and info["rows"] and (info["columns"], info["rows"]) != (self.layout.width, self.layout.height):
                log.info("Curtain reports %dx%d (%d LEDs); layout adjusted", info["columns"], info["rows"], info["pixel_count"])
                self.layout.width, self.layout.height = info["columns"], info["rows"]
            self._emit()
            return
        st = P.parse_notification(data)
        if st is None:
            return
        if st.power is not None:
            self.state["power"] = st.power
        if st.mode == 1 and st.h is not None:
            import colorsys
            r, g, b = colorsys.hsv_to_rgb(st.h / 359, (st.s or 0) / 1000, 1.0)
            self.state.update(mode="color", rgb=[int(r * 255), int(g * 255), int(b * 255)],
                              brightness=round((st.v or 0) / 10), preset=None)
        elif st.mode == 2:
            self.state.update(mode="preset", preset=st.preset,
                              brightness=round((st.preset_brightness or 0) / 10))
        if st.power is False:
            self.set_source("off", "Uit (afstandsbediening)")
        elif st.mode in (1, 2) and self.state["source"]["kind"] not in ("manual", "nowplaying") :
            self.set_source("remote", "Afstandsbediening")
        self._emit()

    # -------------------------------------------------------- connection --

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self.transport.is_connected:
            return
        await self.transport.connect()
        self._initialised = False
        self.state["last_error"] = None
        # Like the app: send the init sequence right after connecting (the curtain seems to expect it).
        if self.send_init:
            try:
                await self._ensure_init()
            except Exception as e:
                log.debug("init after connect: %s", e)
        self._emit()

    async def start(self) -> None:
        """Connect (with background retries) – call on startup."""
        try:
            await self.connect()
        except Exception as e:
            self.state["last_error"] = str(e)
            log.warning("First connection failed: %s (will keep trying)", e)
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        if self._keepalive_task is None and self.keepalive_interval > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Status request (AA 01 00 AB) like the app; keeps the link alive and the state current."""
        while True:
            await asyncio.sleep(self.keepalive_interval)
            if self.transport.is_connected and not self._lock.locked():
                try:
                    async with self._lock:
                        await self._send(P.packet(0x01))
                except Exception as e:
                    log.debug("keepalive: %s", e)

    async def _reconnect_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reconnect_interval)
            if not self.transport.is_connected and not self._lock.locked():
                try:
                    await self.connect()
                    log.info("Reconnected")
                except Exception as e:
                    self.state["last_error"] = str(e)
                    log.debug("reconnect: %s", e)

    async def close(self) -> None:
        await self.stop_animation()
        for t in (self._reconnect_task, self._keepalive_task):
            if t:
                t.cancel()
        self._reconnect_task = self._keepalive_task = None
        await self.transport.disconnect()
        self._emit()

    # ---------------------------------------------------------- sending --

    async def _send(self, pkt: bytes, wait_ack: bool = False) -> None:
        """Unlocked write. Call while holding self._lock."""
        if not self.transport.is_connected:
            await self._connect_locked()
        self._ack.clear()
        await self.transport.write(pkt)
        if wait_ack:
            try:
                await asyncio.wait_for(self._ack.wait(), self.ack_timeout)
            except asyncio.TimeoutError:
                log.debug("No ACK within %.1fs for %s", self.ack_timeout, pkt.hex())
        else:
            await asyncio.sleep(self.packet_delay)

    async def _ensure_init(self) -> None:
        if self.send_init and not self._initialised:
            for pkt in P.INIT_PACKETS:
                await self._send(pkt)
            await self._send(P.time_sync(int(time.time())))
            self._initialised = True

    async def send_raw(self, pkt: bytes) -> None:
        async with self._lock:
            await self._send(pkt)

    async def set_power(self, on: bool) -> None:
        await self.stop_animation()
        async with self._lock:
            await self._send(P.power(on), wait_ack=True)
        self.state["power"] = on
        if not on:
            self.set_source("off", "Uit")
        self._emit()

    async def set_master(self, on: bool) -> None:
        """Master switch. Off = curtain off and automatic sources (now playing) paused."""
        self.state["master"] = bool(on)
        if not on:
            await self.set_power(False)
        else:
            await self.set_power(True)
            self.set_source("unknown", "On, waiting for content")
        self._emit()

    @property
    def master_on(self) -> bool:
        return bool(self.state.get("master", True))

    async def set_rgb(self, r: int, g: int, b: int, brightness: Optional[int] = None) -> None:
        await self.stop_animation()
        if brightness is None:
            brightness = self.state.get("brightness") or 100
        async with self._lock:
            if not self.state.get("power"):
                await self._send(P.power(True), wait_ack=True)
            await self._send(P.color_rgb(r, g, b, brightness), wait_ack=True)
        self.state.update(power=True, mode="color", rgb=[r, g, b], brightness=brightness, preset=None)
        self.set_source("color", "Effen kleur #%02x%02x%02x" % (r, g, b))
        self._emit()

    async def set_brightness(self, brightness: int) -> None:
        brightness = max(0, min(100, int(brightness)))
        if self.state.get("mode") == "preset" and self.state.get("preset"):
            await self.set_preset(self.state["preset"], brightness)
        else:
            r, g, b = self.state.get("rgb") or [255, 255, 255]
            await self.set_rgb(r, g, b, brightness)

    async def set_preset(self, number: int, brightness: Optional[int] = None) -> None:
        await self.stop_animation()
        if brightness is None:
            brightness = self.state.get("brightness") or 100
        async with self._lock:
            if not self.state.get("power"):
                await self._send(P.power(True), wait_ack=True)
            await self._send(P.preset(number, brightness), wait_ack=True)
        self.state.update(power=True, mode="preset", preset=int(number), brightness=brightness)
        self.set_source("preset", f"Preset {number}: {P.PRESETS.get(int(number), '')}".strip(": "))
        self._emit()

    # ------------------------------------------------------------- frames --

    def _packets_for(self, grid: Grid) -> List[bytes]:
        enc = P.PIXEL_ENCODERS[self.pixel_encoder]
        return enc(self.layout.grid_to_pixels(grid), self.transport.mtu)

    async def _show_grid_locked(self, grid: Grid) -> None:
        if not self.state.get("power"):
            await self._send(P.power(True), wait_ack=True)
            self.state["power"] = True
            await asyncio.sleep(0.1)
        await self._ensure_init()
        packets = self._packets_for(grid)
        if self.pixel_encoder == "diy":
            # Exact sequence and timing from the app capture (see protocol.py).
            mv = self.state["movement"]
            await self._send(P.diy_mode(0x03, mv["direction"], mv["brightness"], mv["speed"]))
            await asyncio.sleep(0.0165)
            await self._send(P.diy_mode(0x00, mv["direction"], mv["brightness"], mv["speed"]))
            await asyncio.sleep(0.25)
            for i, pkt in enumerate(packets):
                await self.transport.write(pkt)
                if i < len(packets) - 1:
                    await asyncio.sleep(0.30)
            await asyncio.sleep(1.20)
            await self._send(P.diy_mode(0x04, mv["direction"], mv["brightness"], mv["speed"]))
            await asyncio.sleep(0.02)
            await self._send(P.diy_mode(0x01, mv["direction"], mv["brightness"], mv["speed"]))
            await asyncio.sleep(0.20)
        else:
            for pkt in packets:
                await self._send(pkt)
        self.last_grid = grid
        self.state["mode"] = "pixels"

    async def set_movement(self, direction=0, brightness: Optional[int] = None,
                           speed: Optional[int] = None, apply: bool = True) -> None:
        """Movement/blink/breathe of the current DIY picture (done by the curtain itself)."""
        if isinstance(direction, str):
            direction = P.DIRECTIONS[direction]
        mv = self.state["movement"]
        mv["direction"] = int(direction)
        if brightness is not None:
            mv["brightness"] = max(0, min(100, int(brightness)))
        if speed is not None:
            mv["speed"] = max(0, min(100, int(speed)))
        if apply:
            async with self._lock:
                await self._send(P.diy_mode(0x01, mv["direction"], mv["brightness"], mv["speed"]))
        self._emit()

    async def show_grid(self, grid: Grid, source: tuple[str, str] | None = None) -> None:
        await self.stop_animation()
        async with self._lock:
            await self._show_grid_locked(grid)
        self.set_source(*(source or ("manual", "Own picture")))
        self._emit()

    async def show_solid(self, r: int, g: int, b: int) -> None:
        await self.show_grid(solid(self.layout.width, self.layout.height, (r, g, b)))

    async def test_pixel(self, index: int, color=(255, 255, 255), clear: bool = True) -> None:
        """Light one physical pixel (layout calibration)."""
        pixels = [(0, 0, 0)] * self.layout.count
        pixels[index % self.layout.count] = tuple(color)
        # back to a grid via the inverse mapping so the same upload code is used
        grid = self.layout.blank()
        for i, px in enumerate(pixels):
            if px != (0, 0, 0):
                x, y = self.layout.xy_of(i)
                grid[y][x] = px
        await self.show_grid(grid)

    # ---------------------------------------------------------- animation --

    async def play(self, frames: List[Frame], loop: bool = True, name: str = "animation",
                   source: tuple[str, str] | None = None) -> None:
        await self.stop_animation()
        if not frames:
            return
        src = source or ("manual", name)
        if len(frames) == 1 and not loop:
            await self.show_grid(frames[0][0], source=src)
            return
        self.set_source(*src)
        self._anim_task = asyncio.create_task(self._anim_loop(frames, loop))
        self.state["animation"] = name[:64]
        self._emit()

    async def _anim_loop(self, frames: List[Frame], loop: bool) -> None:
        try:
            while True:
                for grid, dur in frames:
                    t0 = time.monotonic()
                    async with self._lock:
                        await self._show_grid_locked(grid)
                    elapsed = time.monotonic() - t0
                    await asyncio.sleep(max(self.min_frame_time, dur) - min(elapsed, dur))
                if not loop:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.state["last_error"] = f"animatie: {e}"
            log.warning("Animation stopped: %s", e)
        finally:
            self.state["animation"] = None
            self._emit()

    async def stop_animation(self) -> None:
        t = self._anim_task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._anim_task = None
        self.state["animation"] = None
