import asyncio
import io

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from fairycurtain import protocol as P
from fairycurtain import render as R
from fairycurtain.api import create_app
from fairycurtain.config import DEFAULTS
from fairycurtain.device import FairyCurtain
from fairycurtain.layout import Layout
from fairycurtain.transport import FakeTransport


def test_known_packets():
    assert P.power(True).hex() == "aa020101ae"
    assert P.power(False).hex() == "aa020100ad"
    assert P.ACK_POWER == P.packet(0x02)
    assert P.ACK_COLOR == P.packet(0x03)
    assert P.checksum(bytes.fromhex("aa0000")) == 0xAA  # init-pakket AA0000AA


def test_hsv_and_preset_layout():
    pkt = P.color_hsv(359, 1000, 500)
    assert pkt[:4] == bytes([0xAA, 0x03, 0x07, 0x01])
    assert pkt[4:10] == bytes([0x01, 0x67, 0x03, 0xE8, 0x01, 0xF4])
    assert len(pkt) == 11 and pkt[-1] == P.checksum(pkt[:-1])
    pkt = P.preset(20, 60)
    assert pkt[:5] == bytes([0xAA, 0x03, 0x04, 0x02, 20]) and pkt[5:7] == bytes([0x02, 0x58])


def test_pixel_encoder_covers_all_pixels():
    pixels = [(i % 256, 0, 255 - i % 256) for i in range(400)]
    pkts = P.encode_pixels_indexed_da(pixels, mtu=247)
    assert all(p[1] == 0xDA and p[-1] == P.checksum(p[:-1]) and len(p) <= 251 for p in pkts)
    seen = {}
    for p in pkts:
        body = p[4:-1]
        for j in range(0, len(body), 5):
            idx = int.from_bytes(body[j:j + 2], "big")
            seen[idx] = tuple(body[j + 2:j + 5])
    assert seen == {i: pixels[i] for i in range(400)}


def test_notification_parse():
    st = P.parse_notification(bytes.fromhex("aa010b000000" "01" "01" "0167" "03e8" "01f4" "00"))
    assert st.power and st.mode == 1 and (st.h, st.s, st.v) == (359, 1000, 500)
    assert P.parse_notification(P.ACK_POWER) is None and P.is_ack(P.ACK_COLOR)


def test_layout_mappings():
    col = Layout(width=3, height=2, order="column")
    assert [col.index_of(x, y) for y in range(2) for x in range(3)] == [0, 2, 4, 1, 3, 5]
    serp = Layout(width=3, height=2, order="column", serpentine=True)
    assert [serp.index_of(x, y) for y in range(2) for x in range(3)] == [0, 3, 4, 1, 2, 5]
    fx = Layout(width=3, height=2, order="row", flip_x=True)
    assert fx.index_of(0, 0) == 2
    lay = Layout(width=20, height=20)
    assert sorted(lay.index_of(x, y) for y in range(20) for x in range(20)) == list(range(400))
    assert lay.xy_of(21) == (1, 1)


def test_render_image_and_text():
    img = Image.new("RGB", (100, 60), (200, 100, 0))
    g = R.image_to_grid(img, 20, 20, brightness=0.5)
    assert len(g) == 20 and len(g[0]) == 20 and g[0][0] == (100, 50, 0)
    frames = R.text_to_frames("Hoi", 20, 20, speed=10)
    assert len(frames) > 20 and all(len(f[0]) == 20 for f in frames)
    assert any(any(px != (0, 0, 0) for px in row) for g, _ in frames[len(frames)//2:len(frames)//2+1] for row in g)
    buf = io.BytesIO()
    frames_img = [Image.new("RGB", (4, 4), c) for c in [(255, 0, 0), (0, 255, 0)]]
    frames_img[0].save(buf, "GIF", save_all=True, append_images=frames_img[1:], duration=50, loop=0)
    gif = R.gif_bytes_to_frames(buf.getvalue(), 20, 20, brightness=1.0)
    assert len(gif) == 2 and gif[1][0][0][0] == (0, 255, 0)


@pytest.fixture
def setup():
    t = FakeTransport()
    c = FairyCurtain(t, Layout(), packet_delay=0.0, max_fps=100)
    cfg = {**DEFAULTS, "api": {"api_key": "geheim"}, "render": {"brightness": 1.0}}
    return t, c, create_app(c, cfg)


@pytest.mark.asyncio
async def test_api_flow(setup):
    t, c, app = setup
    await c.start()
    h = {"X-API-Key": "geheim"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/status")
        assert r.status_code == 401
        r = await ac.post("/api/power", json={"on": True}, headers=h)
        assert r.json()["power"] is True and t.writes[-1] == P.power(True)
        r = await ac.post("/api/color", json={"hex": "#ff0000", "brightness": 50}, headers=h)
        assert t.writes[-1] == P.color_hsv(0, 1000, 500)
        r = await ac.post("/api/preset", json={"preset": 20}, headers=h)
        assert t.writes[-1] == P.preset(20, 50)
        n = len(t.writes)
        r = await ac.post("/api/frame", json={"hex": ["#010203"] * 400}, headers=h)
        assert r.json()["ok"] and len(t.writes) > n
        assert any(w[1] == 0xDA for w in t.writes[n:])
        r = await ac.post("/api/test/pixel", json={"index": 21}, headers=h)
        assert r.json()["expected_xy"] == [1, 1]
        r = await ac.post("/api/text", json={"text": "Hallo wereld", "speed": 50}, headers=h)
        assert r.json()["frames"] > 5
        await asyncio.sleep(0.2)
        assert c.state["animation"] is not None
        r = await ac.post("/api/animation/stop", headers=h)
        assert r.json()["animation"] is None
        buf = io.BytesIO(); Image.new("RGB", (8, 8), (0, 0, 255)).save(buf, "PNG")
        r = await ac.post("/api/image", files={"file": ("a.png", buf.getvalue(), "image/png")}, headers=h)
        assert r.json() == {"ok": True, "frames": 1, "animated": False}
        assert c.last_grid[0][0] == (0, 0, 255)
        r = await ac.get("/api/frame/preview.png", headers=h)
        assert r.headers["content-type"] == "image/png"
        r = await ac.put("/api/layout", json={"width": 10, "height": 5, "serpentine": True, "save": False}, headers=h)
        assert c.layout.serpentine and c.layout.count == 50
    await c.close()
