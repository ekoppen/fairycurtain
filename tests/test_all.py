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


def test_diy_vectors():
    # from kmich/PROTOCOL.md: green -> 0x3C, blue -> 0x78, D0-vector "aad00403641401fa"
    assert P.rgb_to_pixel_byte(0, 255, 0) == 0x3C and P.rgb_to_pixel_byte(0, 0, 255) == 0x78
    assert P.diy_mode(1, 3, 100, 20).hex() == "aad00403641401fa"
    assert P.diy_mode(3).hex() == "aad0040064640349"
    pk = P.encode_pixels_diy([(255, 0, 0)] * 400)
    assert [len(p) for p in pk] == [242] * 5 + [20] and pk[0][:5].hex() == "aadaee0100"
    info = P.parse_info(bytes.fromhex("aa001f342e372e32323530323500000000000000000000019014011402030207080393"))
    assert info == {"firmware": "4.7.225025", "pixel_count": 400, "columns": 20, "rows": 20, "diy_type": 3}


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
    frames = R.text_to_frames("Hello", 20, 20, speed=10)
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
    c = FairyCurtain(t, Layout(), packet_delay=0.0, max_fps=100, keepalive_interval=0)
    cfg = {**DEFAULTS, "api": {"api_key": "secret"}, "render": {"brightness": 1.0}}
    return t, c, create_app(c, cfg)


@pytest.mark.asyncio
async def test_api_flow(setup):
    t, c, app = setup
    await c.start()
    h = {"X-API-Key": "secret"}
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
        da = [w for w in t.writes[n:] if w[1] == 0xDA]
        assert [len(w) for w in da] == [242] * 5 + [20]
        d0 = [w for w in t.writes[n:] if w[1] == 0xD0]
        assert [w[6] for w in d0] == [0x03, 0x00, 0x04, 0x01]   # order from the capture
        r = await ac.post("/api/test/pixel", json={"index": 21}, headers=h)
        assert r.json()["expected_xy"] == [1, 1]
        r = await ac.post("/api/text", json={"text": "Hello world this is a test", "page_seconds": 0.1}, headers=h)
        assert r.json()["frames"] >= 4 and r.json()["mode"] == "pages"
        r = await ac.post("/api/movement", json={"direction": "loop_left", "speed": 30}, headers=h)
        assert r.json()["direction"] == 9 and P.diy_mode(1, 9, 100, 30) in t.writes
        await asyncio.sleep(0.5)
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


@pytest.mark.asyncio
async def test_input_guards(setup):
    t, c, app = setup
    await c.start()
    h = {"X-API-Key": "secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/power", json={"on": True}, headers={**h, "Origin": "http://evil.example"})
        assert r.status_code == 403
        r = await ac.put("/api/layout", json={"pixel_count": 10**9, "save": False}, headers=h)
        assert r.status_code == 422
        r = await ac.put("/api/layout", json={"order": "diagonal", "save": False}, headers=h)
        assert r.status_code == 422
        r = await ac.post("/api/text", json={"text": "x", "font_size": 5000}, headers=h)
        assert r.status_code == 422
        r = await ac.post("/api/text", json={"text": "x" * 500}, headers=h)
        assert r.status_code == 422
        big = io.BytesIO(); Image.new("RGB", (R.MAX_IMAGE_DIMENSION + 1, 1)).save(big, "PNG")
        r = await ac.post("/api/image", files={"file": ("b.png", big.getvalue(), "image/png")}, headers=h)
        assert r.status_code == 400
    await c.close()


@pytest.mark.asyncio
async def test_host_allowlist_without_key():
    c = FairyCurtain(FakeTransport(), Layout(), keepalive_interval=0)
    app = create_app(c, {**DEFAULTS, "api": {"api_key": None, "allowed_hosts": ["pi.local"]}, "render": {}})
    for host, code in (("evil.example", 403), ("evil.example.lan.evil.com", 403), ("nas.lan", 403), ("pi.local:8321", 200), ("lan01:8321", 200),
                       ("nas.home.arpa", 200), ("192.168.1.5:8321", 200), ("localhost", 200), ("my.pi.example.org", 403)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://{host}") as ac:
            assert (await ac.get("/health")).status_code == code, host


def test_check_ip_blocks_internal_targets():
    from fairycurtain.netsafe import check_ip
    for a in ["127.0.0.1", "::ffff:127.0.0.1", "169.254.169.254", "0.1.2.3", "::1", "224.0.0.1", "240.0.0.1"]:
        with pytest.raises(ValueError):
            check_ip(a)
    check_ip("192.168.1.50")  # LAN stays allowed


@pytest.mark.asyncio
async def test_fetch_image_refuses_bad_urls():
    pytest.importorskip("aiohttp")
    from fairycurtain.netsafe import fetch_image
    for url in ("http://127.0.0.1/x.png", "http://169.254.169.254/latest", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            await fetch_image(url)


def test_ha_origin_match_is_not_a_string_prefix():
    from fairycurtain.nowplaying import _same_origin
    base = "http://ha.local:8123"
    assert _same_origin("http://ha.local:8123/api/media_player_proxy/x", base)
    assert _same_origin("HTTP://HA.LOCAL:8123/x", base)
    for bad in ("http://ha.local:8123@evil.example/x", "http://ha.local:81234/x", "http://ha.local:8123.evil.example/x",
                "https://ha.local:8123/x", "http://evil.example/x"):
        assert not _same_origin(bad, base), bad


@pytest.mark.asyncio
async def test_gallery(setup):
    from fairycurtain import gallery as G
    t, c, app = setup
    await c.start()
    h = {"X-API-Key": "secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/gallery", headers=h)
        items = r.json()["items"]
        assert {"pumpkin", "tree", "heart", "smiley"} <= {i["id"] for i in items}
        assert all(len(i["hex"]) == 400 for i in items)
        r = await ac.post("/api/gallery/pumpkin", headers=h)
        assert r.status_code == 200 and c.last_grid[1][9] == (0, 200, 0)   # the pumpkin stem is green
        assert (await ac.post("/api/gallery/nope", headers=h)).status_code == 404
        # other layout sizes are scaled, not cropped or errored
        assert len(G.grid("heart", 10, 5)) == 5 and len(G.grid("heart", 10, 5)[0]) == 10
    await c.close()
