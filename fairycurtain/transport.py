"""
Transport layer: how the bytes reach the curtain.

  * BleakTransport   – direct BLE connection via a Bluetooth adapter on the
                       host running this service (USB dongle, Pi, …).
  * Esp32Transport   – HTTP to an ESP32 running the `esp32-bridge` firmware; the
                       ESP32 performs the BLE writes. Handy if your server has
                       no Bluetooth or is too far from the curtain.
  * FakeTransport    – for tests/dry runs; only logs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

from . import protocol as P

log = logging.getLogger("fairycurtain.transport")
NotifyCb = Callable[[bytes], None]


class Transport:
    name = "base"
    mtu: int = 23

    def __init__(self) -> None:
        self._notify_cb: Optional[NotifyCb] = None

    def on_notify(self, cb: NotifyCb) -> None:
        self._notify_cb = cb

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def write(self, data: bytes) -> None: ...

    @property
    def is_connected(self) -> bool:
        return False

    @property
    def info(self) -> dict:
        return {"transport": self.name, "connected": self.is_connected, "mtu": self.mtu}


# --------------------------------------------------------------- bleak ----

class BleakTransport(Transport):
    name = "bleak"

    def __init__(self, address: Optional[str] = None, name_prefix: str = P.DEVICE_NAME_PREFIX,
                 adapter: Optional[str] = None, scan_timeout: float = 15.0) -> None:
        super().__init__()
        self.address = address
        self.name_prefix = name_prefix
        self.adapter = adapter
        self.scan_timeout = scan_timeout
        self._client = None
        self._write_char = None

    async def _find_address(self) -> str:
        from bleak import BleakScanner
        kw = {"adapter": self.adapter} if self.adapter else {}
        log.info("Scanning for '%s*' (%.0fs)…", self.name_prefix, self.scan_timeout)
        devices = await BleakScanner.discover(timeout=self.scan_timeout, **kw)
        for d in devices:
            if d.name and d.name.startswith(self.name_prefix):
                log.info("Found: %s (%s)", d.name, d.address)
                return d.address
        raise ConnectionError(f"No device found with name '{self.name_prefix}*'. "
                              "Is the phone app still connected? It must be disconnected.")

    async def connect(self) -> None:
        from bleak import BleakClient
        addr = self.address or await self._find_address()
        kw = {"adapter": self.adapter} if self.adapter else {}
        self._client = BleakClient(addr, disconnected_callback=self._on_disconnect, **kw)
        await self._client.connect()
        self.address = addr
        self._write_char = self._client.services.get_characteristic(P.WRITE_UUID)
        if self._write_char is None:
            raise ConnectionError("Write characteristic not found; is this really a Hello Fairy?")
        try:  # negotiate MTU (BlueZ does this automatically; other backends don't always)
            await self._client._backend._acquire_mtu()  # type: ignore[attr-defined]
        except Exception:
            pass
        self.mtu = getattr(self._client, "mtu_size", 23) or 23
        try:
            await self._client.start_notify(P.NOTIFY_UUID, self._handle_notify)
        except Exception as e:  # notify is optional
            log.warning("Notifications unavailable: %s", e)
        log.info("Connected to %s, MTU=%d", addr, self.mtu)

    def _on_disconnect(self, _client) -> None:
        log.warning("BLE connection lost")

    def _handle_notify(self, _sender, data: bytearray) -> None:
        if self._notify_cb:
            self._notify_cb(bytes(data))

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(P.NOTIFY_UUID)
            except Exception:
                pass
            await self._client.disconnect()

    async def write(self, data: bytes) -> None:
        if not self.is_connected:
            raise ConnectionError("Not connected")
        await self._client.write_gatt_char(self._write_char, data, response=False)

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def info(self) -> dict:
        d = super().info
        d["address"] = self.address
        return d


# --------------------------------------------------------------- esp32 ----

class Esp32Transport(Transport):
    """
    Talks to esp32-bridge/fairy_bridge.ino:
        GET  /status                -> {"connected":true,"mtu":247,"mac":"..."}
        POST /connect?mac=AA:BB..   -> connect (mac optional: scans by name)
        POST /disconnect
        POST /write   body=hex      -> raw GATT write
        GET  /notify                -> {"queue":["aa0200ac", ...]} (and empties the queue)
    """
    name = "esp32"

    def __init__(self, base_url: str, address: Optional[str] = None, poll_interval: float = 2.0) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.address = address
        self.poll_interval = poll_interval
        self._session = None
        self._connected = False
        self._poll_task: Optional[asyncio.Task] = None

    async def _http(self, method: str, path: str, **kw):
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        async with self._session.request(method, self.base_url + path, **kw) as r:
            r.raise_for_status()
            if r.content_type == "application/json":
                return await r.json()
            return await r.text()

    async def connect(self) -> None:
        import aiohttp
        res = None
        if self.address:
            try:
                res = await self._http("POST", "/connect", params={"mac": self.address})
            except aiohttp.ClientResponseError as e:
                if e.status != 503:
                    raise
                log.warning("Connecting to MAC %s failed; bridge will scan by name", self.address)
        if not (isinstance(res, dict) and res.get("connected")):
            res = await self._http("POST", "/connect")
        if not (isinstance(res, dict) and res.get("connected")):
            raise ConnectionError(f"ESP32 bridge could not connect: {res}")
        self.mtu = int(res.get("mtu", 23))
        self.address = res.get("mac", self.address)
        self._connected = True
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_notify())
        log.info("Connected via ESP32 bridge %s (MTU %d)", self.base_url, self.mtu)

    async def _poll_notify(self) -> None:
        while True:
            try:
                res = await self._http("GET", "/notify")
                for h in (res.get("queue") or []) if isinstance(res, dict) else []:
                    if self._notify_cb:
                        self._notify_cb(bytes.fromhex(h))
                self._status_tick = getattr(self, "_status_tick", 0) + 1
                if self._status_tick % 5 == 0:   # status only once per 10 s
                    st = await self._http("GET", "/status")
                    self._connected = bool(isinstance(st, dict) and st.get("connected"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("poll: %s", e)
                self._connected = False
            await asyncio.sleep(self.poll_interval)

    async def disconnect(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        try:
            await self._http("POST", "/disconnect")
        except Exception:
            pass
        self._connected = False
        if self._session:
            await self._session.close()
            self._session = None

    async def write(self, data: bytes) -> None:
        if not self._connected:
            raise ConnectionError("Not connected (ESP32)")
        await self._http("POST", "/write", data=data.hex())

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def info(self) -> dict:
        d = super().info
        d.update(address=self.address, bridge=self.base_url)
        return d


# ---------------------------------------------------------------- fake ----

class FakeTransport(Transport):
    """No hardware: logs all writes and answers with an ACK."""
    name = "fake"
    mtu = 247

    def __init__(self, auto_ack: bool = True) -> None:
        super().__init__()
        self.writes: List[bytes] = []
        self.auto_ack = auto_ack
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def write(self, data: bytes) -> None:
        if not self._connected:
            raise ConnectionError("Not connected (fake)")
        self.writes.append(data)
        log.debug("fake write %s", data.hex())
        if self.auto_ack and self._notify_cb and len(data) > 1 and data[1] in (P.CMD_POWER, P.CMD_COLOR):
            self._notify_cb(P.packet(data[1]))

    @property
    def is_connected(self) -> bool:
        return self._connected


def make_transport(cfg: dict) -> Transport:
    kind = (cfg.get("transport") or "bleak").lower()
    if kind == "bleak":
        return BleakTransport(address=cfg.get("address"), name_prefix=cfg.get("name_prefix", P.DEVICE_NAME_PREFIX),
                              adapter=cfg.get("adapter"), scan_timeout=float(cfg.get("scan_timeout", 15)))
    if kind == "esp32":
        return Esp32Transport(cfg["bridge_url"], address=cfg.get("address"))
    if kind == "fake":
        return FakeTransport()
    raise ValueError(f"Unknown transport: {kind}")
