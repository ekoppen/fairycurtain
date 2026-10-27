import pytest
from httpx import ASGITransport, AsyncClient
from fairycurtain.config import DEFAULTS
from fairycurtain.config_schema import ConfigError, mask_secrets, merge_secrets, validate
import copy


def test_validate_ok_and_errors():
    cfg = copy.deepcopy(DEFAULTS); cfg["device"]["transport"] = "esp32"; cfg["device"]["bridge_url"] = "http://x"
    out = validate(cfg)
    assert out["device"]["transport"] == "esp32" and out["layout"]["width"] == 20
    bad = copy.deepcopy(cfg); bad["layout"]["width"] = 0; bad["device"]["address"] = "nope"; bad["nowplaying"]["enabled"] = True
    with pytest.raises(ConfigError) as e:
        validate(bad)
    assert set(e.value.errors) >= {"layout.width", "device.address", "nowplaying.url"}


def test_mask_and_merge():
    cfg = copy.deepcopy(DEFAULTS); cfg["api"]["api_key"] = "supergeheim123"; cfg["nowplaying"]["headers"] = {"Authorization": "Bearer abcdefghijkl"}
    m = mask_secrets(cfg)
    assert m["api"]["api_key"].startswith("••••") and m["nowplaying"]["headers"]["Authorization"].endswith("ijkl")
    back = merge_secrets(copy.deepcopy(m), cfg)
    assert back["api"]["api_key"] == "supergeheim123" and back["nowplaying"]["headers"]["Authorization"] == "Bearer abcdefghijkl"


@pytest.mark.asyncio
async def test_config_endpoints(tmp_path):
    from fairycurtain.main import build
    cfg = copy.deepcopy(DEFAULTS); cfg["device"]["transport"] = "fake"; cfg["device"]["keepalive_interval"] = 0
    cfg["_path"] = str(tmp_path / "config.yaml")
    app = build(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/config/schema"); assert "device" in r.json()
        r = await ac.get("/api/config"); assert r.json()["config"]["layout"]["width"] == 20
        body = r.json()["config"]; body["layout"]["width"] = 0
        r = await ac.post("/api/config/validate", json=body); assert r.json()["ok"] is False and "layout.width" in r.json()["errors"]
        body["layout"]["width"] = 10; body["render"]["brightness"] = 0.7; body["api"]["port"] = 9000
        r = await ac.put("/api/config", json=body); assert r.status_code == 200 and r.json()["restart_required"] is True
        assert (tmp_path / "config.yaml").exists() and "width: 10" in (tmp_path / "config.yaml").read_text()
        r = await ac.get("/api/status"); assert r.json()["layout"]["width"] == 10
        r = await ac.get("/"); assert r.status_code == 200 and "FairyCurtain" in r.text
