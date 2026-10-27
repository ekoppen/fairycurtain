"""
FairyCurtain: één object dat de verbinding, de status en het versturen van
commando's/frames/animaties beheert. Alle API-, MQTT- en UI-lagen praten
hiermee.
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
                 pixel_encoder: str = "indexed_da",
                 send_init: bool = True,
                 packet_delay: float = 0.03,
                 ack_timeout: float = 1.0,
                 reconnect_interval: float = 10.0,
                 max_fps: float = 8.0) -> None:
        self.transport = transport
        self.layout = layout
        self.pixel_encoder = pixel_encoder
        self.send_init = send_init
        self.packet_delay = packet_delay
        self.ack_timeout = ack_timeout
        self.reconnect_interval = reconnect_interval
        self.min_frame_time = 1.0 / max(0.1, max_fps)

        self.state: dict = {
            "connected": False, "power": None, "mode": None,
            "rgb": [255, 255, 255], "brightness": 100,
            "preset": None, "animation": None, "last_error": None,
            "last_notification": None,
        }
        self.last_grid: Optional[Grid] = None
        self._lock = asyncio.Lock()
        self._ack = asyncio.Event()
        self._anim_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._listeners: List[StateCb] = []
        self._initialised = False
        transport.on_notify(self._on_notify)

    # ------------------------------------------------------------ status --

    def on_state(self, cb: StateCb) -> None:
        self._listeners.append(cb)

    def _emit(self) -> None:
        self.state["connected"] = self.transport.is_connected
        for cb in self._listeners:
            try:
                cb(self.status())
            except Exception as e:  # pragma: no cover
                log.debug("listener: %s", e)

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
        self._emit()

    # -------------------------------------------------------- verbinding --

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self.transport.is_connected:
            return
        await self.transport.connect()
        self._initialised = False
        self.state["last_error"] = None
        # Net als de app: direct na verbinden de init-reeks sturen (gordijn lijkt dit te verwachten).
        if self.send_init:
            try:
                await self._ensure_init()
            except Exception as e:
                log.debug("init na verbinden: %s", e)
        self._emit()

    async def start(self) -> None:
        """Verbind (met retries op de achtergrond) – aanroepen bij opstarten."""
        try:
            await self.connect()
        except Exception as e:
            self.state["last_error"] = str(e)
            log.warning("Eerste verbinding mislukt: %s (blijf proberen)", e)
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reconnect_interval)
            if not self.transport.is_connected and not self._lock.locked():
                try:
                    await self.connect()
                    log.info("Opnieuw verbonden")
                except Exception as e:
                    self.state["last_error"] = str(e)
                    log.debug("reconnect: %s", e)

    async def close(self) -> None:
        await self.stop_animation()
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self.transport.disconnect()
        self._emit()

    # ---------------------------------------------------------- versturen --

    async def _send(self, pkt: bytes, wait_ack: bool = False) -> None:
        """Niet-gelockte write. Roep aan binnen self._lock."""
        if not self.transport.is_connected:
            await self._connect_locked()
        self._ack.clear()
        await self.transport.write(pkt)
        if wait_ack:
            try:
                await asyncio.wait_for(self._ack.wait(), self.ack_timeout)
            except asyncio.TimeoutError:
                log.debug("Geen ACK binnen %.1fs voor %s", self.ack_timeout, pkt.hex())
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
        self._emit()

    async def set_rgb(self, r: int, g: int, b: int, brightness: Optional[int] = None) -> None:
        await self.stop_animation()
        if brightness is None:
            brightness = self.state.get("brightness") or 100
        async with self._lock:
            if not self.state.get("power"):
                await self._send(P.power(True), wait_ack=True)
            await self._send(P.color_rgb(r, g, b, brightness), wait_ack=True)
        self.state.update(power=True, mode="color", rgb=[r, g, b], brightness=brightness, preset=None)
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
        self._emit()

    # ------------------------------------------------------------- frames --

    def _packets_for(self, grid: Grid) -> List[bytes]:
        enc = P.PIXEL_ENCODERS[self.pixel_encoder]
        return enc(self.layout.grid_to_pixels(grid), self.transport.mtu)

    async def _show_grid_locked(self, grid: Grid) -> None:
        if not self.state.get("power"):
            await self._send(P.power(True), wait_ack=True)
            self.state["power"] = True
        await self._ensure_init()
        for pkt in self._packets_for(grid):
            await self._send(pkt)
        self.last_grid = grid
        self.state["mode"] = "pixels"

    async def show_grid(self, grid: Grid) -> None:
        await self.stop_animation()
        async with self._lock:
            await self._show_grid_locked(grid)
        self._emit()

    async def show_solid(self, r: int, g: int, b: int) -> None:
        await self.show_grid(solid(self.layout.width, self.layout.height, (r, g, b)))

    async def test_pixel(self, index: int, color=(255, 255, 255), clear: bool = True) -> None:
        """Eén fysieke pixel aanzetten (kalibratie van de layout)."""
        await self.stop_animation()
        pixels = [(0, 0, 0)] * self.layout.count if clear else None
        async with self._lock:
            if not self.state.get("power"):
                await self._send(P.power(True), wait_ack=True)
            await self._ensure_init()
            enc = P.PIXEL_ENCODERS[self.pixel_encoder]
            if pixels is None:
                pixels = [(0, 0, 0)] * self.layout.count
            pixels[index % self.layout.count] = tuple(color)
            for pkt in enc(pixels, self.transport.mtu):
                await self._send(pkt)
        self.state["mode"] = "pixels"
        self._emit()

    # ---------------------------------------------------------- animatie --

    async def play(self, frames: List[Frame], loop: bool = True, name: str = "animation") -> None:
        await self.stop_animation()
        if not frames:
            return
        if len(frames) == 1 and not loop:
            await self.show_grid(frames[0][0])
            return
        self._anim_task = asyncio.create_task(self._anim_loop(frames, loop))
        self.state["animation"] = name
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
            log.warning("Animatie gestopt: %s", e)
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
