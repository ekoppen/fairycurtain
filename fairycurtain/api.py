"""
REST API (FastAPI) + web editor.

All endpoints live under /api. Optionally protected with the `X-API-Key` header.
Interactive documentation: http://host:8321/docs
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import logging
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import gallery as GAL
from . import protocol as P
from . import render as R
from .config import save_config, save_layout
from .config_schema import SCHEMA, ConfigError, mask_secrets, merge_secrets, validate
from .device import FairyCurtain
from .layout import Layout

log = logging.getLogger("fairycurtain.api")
STATIC = Path(__file__).parent / "static"


# ------------------------------------------------------------- models ----

class PowerIn(BaseModel):
    on: bool


class ColorIn(BaseModel):
    r: Optional[int] = Field(None, ge=0, le=255)
    g: Optional[int] = Field(None, ge=0, le=255)
    b: Optional[int] = Field(None, ge=0, le=255)
    hex: Optional[str] = None            # "#ff8800"
    brightness: Optional[int] = Field(None, ge=0, le=100)

    def rgb(self):
        if self.hex:
            return R.hex_to_rgb(self.hex)
        if None in (self.r, self.g, self.b):
            raise HTTPException(400, "Provide r,g,b or hex")
        return (self.r, self.g, self.b)


class BrightnessIn(BaseModel):
    brightness: int = Field(..., ge=0, le=100)


class PresetIn(BaseModel):
    preset: int = Field(..., ge=1, le=58)
    brightness: Optional[int] = Field(None, ge=0, le=100)


class FrameIn(BaseModel):
    """pixels: rows of [r,g,b] (grid[y][x]), or a flat list of hex colors."""
    pixels: Optional[List[List[List[int]]]] = None
    hex: Optional[List[str]] = None       # width*height items, row by row
    brightness: Optional[float] = Field(None, ge=0, le=1)


class TextIn(BaseModel):
    text: str = Field(..., max_length=R.MAX_TEXT_CHARS)
    color: str = "#ffffff"
    background: str = "#000000"
    mode: str = Field("pages", description="pages (default, ~3 characters per page) | scroll (slow: ~2.5 s per step)")
    page_seconds: float = Field(4.0, gt=0)
    scroll: Optional[bool] = None          # legacy: True == mode scroll
    speed: float = Field(12.0, gt=0, description="scroll only: pixels per step interval")
    loop: bool = True
    brightness: Optional[float] = Field(None, ge=0, le=1)
    font_size: Optional[int] = Field(None, ge=R.MIN_FONT_SIZE, le=R.MAX_FONT_SIZE)
    movement: Optional[str] = Field(None, description="movement performed by the curtain itself, e.g. loop_left, blink, breathe")


class MovementIn(BaseModel):
    direction: str | int = Field("still", description=f"one of {list(P.DIRECTIONS)} or 0-10")
    brightness: Optional[int] = Field(None, ge=0, le=100)
    speed: Optional[int] = Field(None, ge=0, le=100)


class ImageB64In(BaseModel):
    data: str                              # base64 (with or without data: prefix)
    fit: str = "cover"
    brightness: Optional[float] = Field(None, ge=0, le=1)
    loop: bool = True


class RawIn(BaseModel):
    hex: str


class TestPixelIn(BaseModel):
    index: int = Field(..., ge=0)
    color: str = "#ffffff"


class LayoutIn(BaseModel):
    width: int = Field(20, ge=1, le=256)
    height: int = Field(20, ge=1, le=256)
    order: Literal["column", "row"] = "column"
    serpentine: bool = False
    flip_x: bool = False
    flip_y: bool = False
    pixel_count: Optional[int] = Field(None, ge=1, le=65536)
    save: bool = True


# ------------------------------------------------------------ factory ----

def create_app(curtain: FairyCurtain, cfg: dict) -> FastAPI:
    app = FastAPI(title="FairyCurtain", version="1.0", description="Hello Fairy LED curtain API")
    render_cfg = cfg.get("render", {})
    api_key = (cfg.get("api") or {}).get("api_key")

    async def auth(x_api_key: Optional[str] = Header(None), key: Optional[str] = None):
        if api_key and not any(k and hmac.compare_digest(k.encode(), api_key.encode()) for k in (x_api_key, key)):
            raise HTTPException(401, "Invalid API key")

    dep = [Depends(auth)]

    allowed_hosts = {"localhost", *(h.lower() for h in (cfg.get("api") or {}).get("allowed_hosts") or [])}

    def _host_ok(host_header: str) -> bool:
        name = urlsplit("//" + host_header).hostname or ""
        if name in allowed_hosts:
            return True
        try:  # IP literals can't be DNS-rebound; hostnames can
            ipaddress.ip_address(name)
            return True
        except ValueError:
            return False

    @app.middleware("http")
    async def browser_guard(request: Request, call_next):
        # Without an API key, browsers are the attack path (CSRF / DNS rebinding):
        # refuse unknown Host names and cross-origin writes. With a key, the header is the guard.
        if not api_key and not _host_ok(request.headers.get("host", "")):
            return JSONResponse({"detail": "Host not allowed; add it to api.allowed_hosts or set api_key"},
                                status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and \
                urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        return await call_next(request)
    W, H = curtain.layout.width, curtain.layout.height

    def _bright(v: Optional[float]) -> float:
        return float(render_cfg.get("brightness", 0.5)) if v is None else v

    def _wh():
        return curtain.layout.width, curtain.layout.height

    # ---- gallery
    @app.get("/api/gallery", dependencies=dep)
    async def gallery_list():
        return {"items": GAL.listing()}

    @app.post("/api/gallery/{item_id}", dependencies=dep)
    async def gallery_show(item_id: str):
        w, h = _wh()
        grid = GAL.grid(item_id, w, h)
        if grid is None:
            raise HTTPException(404, "Unknown gallery item")
        await curtain.show_grid(grid)
        return {"ok": True, "id": item_id}

    # ---- status
    @app.get("/api/status", dependencies=dep)
    async def status():
        return curtain.status()

    @app.get("/api/presets", dependencies=dep)
    async def presets():
        return {"presets": [{"number": n, "name": P.PRESETS.get(n, f"Preset {n}")} for n in range(1, 59)]}

    # ---- basic controls
    @app.post("/api/power", dependencies=dep)
    async def power(body: PowerIn):
        """Curtain on/off without changing the master switch (e.g. from HA)."""
        await curtain.set_power(body.on)
        return curtain.status()

    @app.post("/api/master", dependencies=dep)
    async def master(body: PowerIn):
        """Master switch: off = everything off and now playing paused; on = resume."""
        await curtain.set_master(body.on)
        return curtain.status()

    @app.post("/api/color", dependencies=dep)
    async def color(body: ColorIn):
        r, g, b = body.rgb()
        await curtain.set_rgb(r, g, b, body.brightness)
        return curtain.status()

    @app.post("/api/brightness", dependencies=dep)
    async def brightness(body: BrightnessIn):
        await curtain.set_brightness(body.brightness)
        return curtain.status()

    @app.post("/api/preset", dependencies=dep)
    async def preset(body: PresetIn):
        await curtain.set_preset(body.preset, body.brightness)
        return curtain.status()

    # ---- pixels / frames
    @app.post("/api/frame", dependencies=dep)
    async def frame(body: FrameIn):
        w, h = _wh()
        if body.pixels:
            grid = [[tuple(px) for px in row] for row in body.pixels]
        elif body.hex and len(body.hex) >= w * h:
            grid = [[R.hex_to_rgb(body.hex[y * w + x]) for x in range(w)] for y in range(h)]
        else:
            raise HTTPException(400, f"Provide 'pixels' (rows) or 'hex' with {w*h} colors")
        if body.brightness is not None:
            f = body.brightness
            grid = [[tuple(int(c * f) for c in px) for px in row] for row in grid]
        await curtain.show_grid(grid)
        return {"ok": True, "packets": len(curtain._packets_for(grid))}

    @app.post("/api/image", dependencies=dep)
    async def image(file: UploadFile = File(...), fit: str = Form("cover"),
                    brightness: Optional[float] = Form(None), loop: bool = Form(True)):
        data = await file.read(R.MAX_IMAGE_BYTES + 1)
        return await _show_image_bytes(data, fit, brightness, loop, (file.filename or "image")[:64])

    @app.post("/api/image/base64", dependencies=dep)
    async def image_b64(body: ImageB64In):
        s = body.data.split(",", 1)[-1]
        if len(s) > R.MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise HTTPException(413, "Image too large")
        return await _show_image_bytes(base64.b64decode(s), body.fit, body.brightness, body.loop, "image")

    async def _show_image_bytes(data: bytes, fit: str, brightness: Optional[float], loop: bool, name: str):
        if len(data) > R.MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image too large")
        w, h = _wh()
        kw = dict(fit=fit, brightness=_bright(brightness), gamma=float(render_cfg.get("gamma", 1.0)))
        try:
            img = R.bytes_to_image(data)
        except Exception as e:
            raise HTTPException(400, f"Not a valid image: {e}")
        if getattr(img, "is_animated", False):
            frames = R.gif_bytes_to_frames(data, w, h, **kw)
            await curtain.play(frames, loop=loop, name=name)
            return {"ok": True, "frames": len(frames), "animated": True}
        grid = R.image_to_grid(img, w, h, **kw)
        await curtain.show_grid(grid)
        return {"ok": True, "frames": 1, "animated": False}

    @app.post("/api/text", dependencies=dep)
    async def text(body: TextIn):
        w, h = _wh()
        if body.movement is not None:
            await curtain.set_movement(body.movement, apply=False)
        mode = "scroll" if body.scroll else body.mode
        if mode == "scroll":
            frames = R.text_to_frames(body.text, w, h, color=R.hex_to_rgb(body.color),
                                      background=R.hex_to_rgb(body.background), scroll=True,
                                      speed=body.speed, font_path=render_cfg.get("font_path"),
                                      font_size=body.font_size)
        else:
            frames = R.text_to_pages(body.text, w, h, color=R.hex_to_rgb(body.color),
                                     background=R.hex_to_rgb(body.background), page_seconds=body.page_seconds,
                                     font_path=render_cfg.get("font_path"), font_size=body.font_size)
        await curtain.play(frames, loop=body.loop, name=f"text:{body.text[:20]}")
        return {"ok": True, "frames": len(frames), "mode": mode}

    @app.post("/api/movement", dependencies=dep)
    async def movement(body: MovementIn):
        """Make the curtain move/blink/breathe the current picture (no new upload needed)."""
        try:
            await curtain.set_movement(body.direction, body.brightness, body.speed)
        except KeyError:
            raise HTTPException(400, f"Unknown direction; choose from {list(P.DIRECTIONS)} or 0-10")
        return curtain.state["movement"]

    @app.post("/api/animation/stop", dependencies=dep)
    async def stop():
        await curtain.stop_animation()
        return curtain.status()

    @app.post("/api/clear", dependencies=dep)
    async def clear():
        await curtain.show_solid(0, 0, 0)
        return {"ok": True}

    def _quantized(grid):
        """As the curtain shows it: per pixel hue / white / off."""
        return [[P.pixel_byte_to_rgb(P.rgb_to_pixel_byte(*px)) for px in row] for row in grid]

    @app.get("/api/frame/preview.png", dependencies=dep)
    async def preview(raw: bool = False):
        grid = curtain.last_grid or curtain.layout.blank()
        return Response(R.grid_to_png(grid if raw else _quantized(grid), 12), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/frame", dependencies=dep)
    async def current_frame(raw: bool = False):
        """Last sent frame as a hex list (row by row), as /api/frame accepts it."""
        grid = curtain.last_grid or curtain.layout.blank()
        g = grid if raw else _quantized(grid)
        return {"width": curtain.layout.width, "height": curtain.layout.height,
                "hex": ["#%02x%02x%02x" % tuple(px) for row in g for px in row],
                "animation": curtain.state.get("animation"), "mode": curtain.state.get("mode")}

    # ---- calibration / debug
    @app.post("/api/test/pixel", dependencies=dep)
    async def test_pixel(body: TestPixelIn):
        await curtain.test_pixel(body.index, R.hex_to_rgb(body.color))
        try:
            xy = curtain.layout.xy_of(body.index)
        except ValueError:
            xy = None
        return {"ok": True, "index": body.index, "expected_xy": xy}

    @app.post("/api/raw", dependencies=dep)
    async def raw(body: RawIn):
        try:
            pkt = bytes.fromhex(body.hex.replace(" ", ""))
        except ValueError:
            raise HTTPException(400, "Invalid hex")
        await curtain.send_raw(pkt)
        return {"ok": True, "sent": pkt.hex()}

    @app.post("/api/raw/packet", dependencies=dep)
    async def raw_packet(cmd: int, payload_hex: str = ""):
        """Builds AA CMD LEN PAYLOAD CS and sends it."""
        pkt = P.packet(cmd, bytes.fromhex(payload_hex.replace(" ", "")))
        await curtain.send_raw(pkt)
        return {"ok": True, "sent": pkt.hex()}

    @app.get("/api/layout", dependencies=dep)
    async def get_layout():
        return curtain.layout.to_dict()

    @app.put("/api/layout", dependencies=dep)
    async def put_layout(body: LayoutIn):
        d = body.model_dump(exclude={"save"})
        curtain.layout = Layout.from_dict(d)
        if body.save:
            try:
                save_layout(cfg, d)
            except Exception as e:
                log.warning("Layout not saved: %s", e)
        return curtain.layout.to_dict()

    @app.post("/api/encoder", dependencies=dep)
    async def set_encoder(name: str):
        if name not in P.PIXEL_ENCODERS:
            raise HTTPException(400, f"Unknown; choose from {list(P.PIXEL_ENCODERS)}")
        curtain.pixel_encoder = name
        return {"pixel_encoder": name}

    @app.post("/api/connect", dependencies=dep)
    async def connect():
        try:
            await curtain.connect()
        except Exception as e:
            raise HTTPException(503, str(e))
        return curtain.status()

    # ---- now playing
    @app.get("/api/nowplaying", dependencies=dep)
    async def nowplaying_status():
        np = getattr(app.state, "nowplaying", None)
        return np.status() if np else {"enabled": False}

    @app.post("/api/nowplaying/enable", dependencies=dep)
    async def nowplaying_enable(enabled: bool = True):
        np = getattr(app.state, "nowplaying", None)
        if not np:
            raise HTTPException(503, "nowplaying not configured")
        np.enabled = enabled
        if not enabled:
            await curtain.stop_animation()
        return np.status()

    @app.post("/api/nowplaying/push", dependencies=dep)
    async def nowplaying_push(body: dict):
        """Push from your own app: {"title":..,"artist":..,"image":url,"playing":true}."""
        np = getattr(app.state, "nowplaying", None)
        if not np:
            raise HTTPException(503, "nowplaying not configured")
        np.enabled = True
        await np.update({"title": body.get("title"), "artist": body.get("artist"),
                         "image": body.get("image"), "playing": body.get("playing", True)})
        return np.status()

    # ---- config-editor
    @app.get("/api/config/schema", dependencies=dep)
    async def config_schema():
        return SCHEMA

    @app.get("/api/config", dependencies=dep)
    async def config_get():
        return {"config": mask_secrets({k: v for k, v in cfg.items() if not k.startswith("_")}),
                "path": cfg.get("_path"), "restart_pending": bool(getattr(app.state, "restart_pending", False))}

    @app.post("/api/config/validate", dependencies=dep)
    async def config_validate(body: dict):
        try:
            validate(merge_secrets(body, cfg))
        except ConfigError as e:
            return {"ok": False, "errors": e.errors}
        return {"ok": True, "errors": {}}

    @app.put("/api/config", dependencies=dep)
    async def config_put(body: dict):
        merged = merge_secrets(body, cfg)
        try:
            clean = validate(merged)
        except ConfigError as e:
            raise HTTPException(422, {"errors": e.errors})
        # apply live whatever can be applied live
        needs_restart = False
        for section, sspec in SCHEMA.items():
            for fname, fspec in sspec.get("fields", {}).items():
                old = (cfg.get(section) or {}).get(fname)
                new = clean.get(section, {}).get(fname)
                if old != new and fspec.get("restart"):
                    needs_restart = True
        if not needs_restart and clean.get("log_level") != cfg.get("log_level"):
            needs_restart = True
        try:
            save_config(cfg, clean)
        except Exception as e:
            raise HTTPException(500, f"Saving failed: {e}")
        # live bijwerken
        cfg.update({k: v for k, v in clean.items()})
        curtain.layout = Layout.from_dict(clean["layout"])
        curtain.pixel_encoder = clean["device"]["pixel_encoder"]
        curtain.send_init = clean["device"]["send_init"]
        curtain.packet_delay = clean["device"]["packet_delay"]
        curtain.min_frame_time = 1.0 / max(0.1, clean["device"]["max_fps"])
        render_cfg.update(clean["render"])
        np = getattr(app.state, "nowplaying", None)
        if np:
            np.cfg.update(clean["nowplaying"])
            np.enabled = bool(clean["nowplaying"]["enabled"])
        app.state.restart_pending = needs_restart
        return {"ok": True, "restart_required": needs_restart}

    @app.post("/api/restart", dependencies=dep)
    async def restart():
        """Stops the process; Docker (restart: unless-stopped) starts it again."""
        import os, signal
        async def _die():
            await asyncio.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)
        asyncio.create_task(_die())
        return {"ok": True, "message": "Service restarting…"}

    # ---- UI
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health", include_in_schema=False)
    async def health():
        return JSONResponse({"ok": True, "connected": curtain.transport.is_connected})

    return app
