"""
REST-API (FastAPI) + webeditor.

Alle endpoints zitten onder /api. Optioneel beveiligd met header `X-API-Key`.
Interactieve documentatie: http://host:8321/docs
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import protocol as P
from . import render as R
from .config import save_layout
from .device import FairyCurtain
from .layout import Layout

log = logging.getLogger("fairycurtain.api")
STATIC = Path(__file__).parent / "static"


# ----------------------------------------------------------- modellen ----

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
            raise HTTPException(400, "Geef r,g,b of hex")
        return (self.r, self.g, self.b)


class BrightnessIn(BaseModel):
    brightness: int = Field(..., ge=0, le=100)


class PresetIn(BaseModel):
    preset: int = Field(..., ge=1, le=58)
    brightness: Optional[int] = Field(None, ge=0, le=100)


class FrameIn(BaseModel):
    """pixels: rijen van rijen [r,g,b]  (grid[y][x]) of platte lijst van hex-kleuren."""
    pixels: Optional[List[List[List[int]]]] = None
    hex: Optional[List[str]] = None       # width*height items, rij voor rij
    brightness: Optional[float] = Field(None, ge=0, le=1)


class TextIn(BaseModel):
    text: str
    color: str = "#ffffff"
    background: str = "#000000"
    mode: str = Field("pages", description="pages (standaard, ~3 tekens per pagina) | scroll (traag: ~2,5 s per stap)")
    page_seconds: float = Field(4.0, gt=0)
    scroll: Optional[bool] = None          # legacy: True == mode scroll
    speed: float = Field(12.0, gt=0, description="alleen bij scroll: pixels per stap-interval")
    loop: bool = True
    brightness: Optional[float] = Field(None, ge=0, le=1)
    font_size: Optional[int] = None
    movement: Optional[str] = Field(None, description="beweging door het gordijn zelf, bv. loop_left, blink, breathe")


class MovementIn(BaseModel):
    direction: str | int = Field("still", description=f"één van {list(P.DIRECTIONS)} of 0-10")
    brightness: Optional[int] = Field(None, ge=0, le=100)
    speed: Optional[int] = Field(None, ge=0, le=100)


class ImageB64In(BaseModel):
    data: str                              # base64 (met of zonder data:-prefix)
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
    order: str = "column"
    serpentine: bool = False
    flip_x: bool = False
    flip_y: bool = False
    pixel_count: Optional[int] = None
    save: bool = True


# ------------------------------------------------------------ fabriek ----

def create_app(curtain: FairyCurtain, cfg: dict) -> FastAPI:
    app = FastAPI(title="FairyCurtain", version="1.0", description="Hello Fairy LED-gordijn API")
    render_cfg = cfg.get("render", {})
    api_key = (cfg.get("api") or {}).get("api_key")

    async def auth(x_api_key: Optional[str] = Header(None), key: Optional[str] = None):
        if api_key and x_api_key != api_key and key != api_key:
            raise HTTPException(401, "Ongeldige API-key")

    dep = [Depends(auth)]
    W, H = curtain.layout.width, curtain.layout.height

    def _bright(v: Optional[float]) -> float:
        return float(render_cfg.get("brightness", 0.5)) if v is None else v

    def _wh():
        return curtain.layout.width, curtain.layout.height

    # ---- status
    @app.get("/api/status", dependencies=dep)
    async def status():
        return curtain.status()

    @app.get("/api/presets", dependencies=dep)
    async def presets():
        return {"presets": [{"number": n, "name": P.PRESETS.get(n, f"Preset {n}")} for n in range(1, 59)]}

    # ---- basisbediening
    @app.post("/api/power", dependencies=dep)
    async def power(body: PowerIn):
        await curtain.set_power(body.on)
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
            raise HTTPException(400, f"Geef 'pixels' (rijen) of 'hex' met {w*h} kleuren")
        if body.brightness is not None:
            f = body.brightness
            grid = [[tuple(int(c * f) for c in px) for px in row] for row in grid]
        await curtain.show_grid(grid)
        return {"ok": True, "packets": len(curtain._packets_for(grid))}

    @app.post("/api/image", dependencies=dep)
    async def image(file: UploadFile = File(...), fit: str = Form("cover"),
                    brightness: Optional[float] = Form(None), loop: bool = Form(True)):
        data = await file.read()
        return await _show_image_bytes(data, fit, brightness, loop, file.filename or "image")

    @app.post("/api/image/base64", dependencies=dep)
    async def image_b64(body: ImageB64In):
        s = body.data.split(",", 1)[-1]
        return await _show_image_bytes(base64.b64decode(s), body.fit, body.brightness, body.loop, "image")

    async def _show_image_bytes(data: bytes, fit: str, brightness: Optional[float], loop: bool, name: str):
        w, h = _wh()
        kw = dict(fit=fit, brightness=_bright(brightness), gamma=float(render_cfg.get("gamma", 1.0)))
        try:
            img = R.bytes_to_image(data)
        except Exception as e:
            raise HTTPException(400, f"Geen geldige afbeelding: {e}")
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
        """Laat het gordijn het huidige plaatje bewegen/knipperen/ademen (geen nieuwe upload nodig)."""
        try:
            await curtain.set_movement(body.direction, body.brightness, body.speed)
        except KeyError:
            raise HTTPException(400, f"Onbekende richting; keuze uit {list(P.DIRECTIONS)} of 0-10")
        return curtain.state["movement"]

    @app.post("/api/animation/stop", dependencies=dep)
    async def stop():
        await curtain.stop_animation()
        return curtain.status()

    @app.post("/api/clear", dependencies=dep)
    async def clear():
        await curtain.show_solid(0, 0, 0)
        return {"ok": True}

    @app.get("/api/frame/preview.png", dependencies=dep)
    async def preview():
        grid = curtain.last_grid or curtain.layout.blank()
        return Response(R.grid_to_png(grid, 12), media_type="image/png")

    # ---- kalibratie / debug
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
            raise HTTPException(400, "Ongeldige hex")
        await curtain.send_raw(pkt)
        return {"ok": True, "sent": pkt.hex()}

    @app.post("/api/raw/packet", dependencies=dep)
    async def raw_packet(cmd: int, payload_hex: str = ""):
        """Bouwt AA CMD LEN PAYLOAD CS en verstuurt."""
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
                log.warning("Layout niet opgeslagen: %s", e)
        return curtain.layout.to_dict()

    @app.post("/api/encoder", dependencies=dep)
    async def set_encoder(name: str):
        if name not in P.PIXEL_ENCODERS:
            raise HTTPException(400, f"Onbekend; keuze uit {list(P.PIXEL_ENCODERS)}")
        curtain.pixel_encoder = name
        return {"pixel_encoder": name}

    @app.post("/api/connect", dependencies=dep)
    async def connect():
        try:
            await curtain.connect()
        except Exception as e:
            raise HTTPException(503, str(e))
        return curtain.status()

    # ---- UI
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health", include_in_schema=False)
    async def health():
        return JSONResponse({"ok": True, "connected": curtain.transport.is_connected})

    return app
