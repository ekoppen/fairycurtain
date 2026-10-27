# FairyCurtain

Je **Hello Fairy** LED-gordijn (Bluetooth, controller KR-026) zelf aansturen: REST-API, webeditor om te tekenen, afbeeldingen/GIF's/scrollende tekst, en een kant-en-klare koppeling met Home Assistant via MQTT.

```
┌──────────────┐  HTTP/MQTT  ┌──────────────┐   BLE    ┌──────────────┐
│ HA / jouw    │ ──────────▶ │ FairyCurtain │ ───────▶ │ Hello Fairy  │
│ apps / UI    │             │  (Python)    │          │  gordijn     │
└──────────────┘             └──────┬───────┘          └──────────────┘
                                    │  óf via HTTP naar een ESP32-bridge
                                    └──────▶ ESP32 (fairy_bridge.ino) ──BLE──▶ gordijn
```

## Wat zit erin

| Onderdeel | Pad |
|---|---|
| Protocol (pakketten, checksum, pixel-encoders) | `fairycurtain/protocol.py` |
| Raster ↔ pixelindex (layout, kalibreerbaar) | `fairycurtain/layout.py` |
| Afbeelding/GIF/tekst → 20×20 frames | `fairycurtain/render.py` |
| Verbinding, status, animaties | `fairycurtain/device.py`, `transport.py` |
| REST-API + webeditor | `fairycurtain/api.py`, `static/index.html` |
| MQTT + HA-discovery | `fairycurtain/mqtt.py` |
| ESP32-bridge firmware (optioneel) | `esp32-bridge/fairy_bridge.ino` |
| App-sniffer om het pixelprotocol te verifiëren | `tools/btsnoop_writes.py` |
| HA-voorbeelden | `homeassistant/voorbeelden.yaml` |

## Snel starten

```bash
cp config.example.yaml config.yaml      # pas aan
pip install .                           # of: docker compose up -d --build
fairycurtain                            # http://localhost:8321
```

Open `http://<host>:8321` voor de editor, `http://<host>:8321/docs` voor de API-documentatie.

**Belangrijk:** het gordijn accepteert één Bluetooth-verbinding. Sluit de Hello Fairy-app op je telefoon (of zet daar Bluetooth uit) voordat je de service start.

## Waar draait het? Twee opties

**A. Direct (transport: `bleak`)** — de service draait op een machine met een Bluetooth-adapter, binnen ±5–8 m van het gordijn. Op Proxmox: USB-BT-dongle doorgeven aan de LXC/VM met Docker (`network_mode: host` + D-Bus, zie `docker-compose.yml`). Of een Raspberry Pi.

**B. Via ESP32 (transport: `esp32`)** — flash `esp32-bridge/fairy_bridge.ino` op een ESP32 (wifi-gegevens invullen; NimBLE-Arduino installeren). Zet hem bij het gordijn, zet in `config.yaml` `transport: esp32` en `bridge_url: http://fairy-bridge.local`. De service kan dan overal in je homelab draaien zonder Bluetooth. Dit is voor jouw setup waarschijnlijk de prettigste optie.

## API-overzicht

Alle endpoints onder `/api`, JSON. Met `api_key` ingesteld: header `X-API-Key`.

| Endpoint | Doel | Voorbeeld-body |
|---|---|---|
| `GET /status` | status/verbinding | |
| `POST /power` | aan/uit | `{"on": true}` |
| `POST /color` | effen kleur | `{"hex":"#ff8800","brightness":60}` of `{"r":255,"g":0,"b":0}` |
| `POST /brightness` | helderheid 0–100 | `{"brightness": 40}` |
| `POST /preset` | ingebouwd effect 1–58 | `{"preset": 20}` |
| `POST /frame` | eigen raster | `{"hex": ["#ff0000", … 400×]}` of `{"pixels": [[[r,g,b],…],…]}` |
| `POST /image` | multipart upload (PNG/JPG/GIF) | velden `file`, `fit`, `brightness` |
| `POST /image/base64` | afbeelding als base64 | `{"data":"…","fit":"cover"}` |
| `POST /text` | (scrollende) tekst | `{"text":"Hoi","color":"#00ffff","speed":12}` |
| `POST /animation/stop` | animatie stoppen | |
| `POST /clear` | alles zwart | |
| `GET /frame/preview.png` | laatst getoonde frame | |
| `POST /test/pixel` | één fysieke LED (kalibratie) | `{"index": 0}` |
| `PUT /layout` | raster-mapping aanpassen/opslaan | `{"width":20,"height":20,"order":"column","serpentine":false}` |
| `POST /raw` | ruwe bytes | `{"hex":"aa020101ae"}` |

```bash
curl -X POST localhost:8321/api/text -H 'Content-Type: application/json' \
     -d '{"text":"Welkom!","color":"#ff66cc","speed":14}'
curl -X POST localhost:8321/api/image -F file=@pompoen.gif -F brightness=0.4
```

## Home Assistant

Zet in `config.yaml` `mqtt.enabled: true` met je broker. Via MQTT-discovery verschijnt automatisch een apparaat **LED-gordijn** met:

- `light.led_gordijn` – aan/uit, RGB-kleur, helderheid, effectenlijst (de 58 presets);
- `text.led_gordijn_tekst` – typ tekst in HA en hij scrollt over het gordijn;
- `button.led_gordijn_stop_animatie`.

Voor automatiseringen zijn er command-topics (`fairycurtain/cmd/text`, `/cmd/image`, `/cmd/frame`, `/cmd/raw`, `/cmd/stop`). Zonder MQTT werkt alles ook met `rest_command`. Beide staan uitgewerkt in `homeassistant/voorbeelden.yaml`.

## Eerste keer: kalibreren

1. Start de service, open de editor, controleer dat de status "verbonden" zegt.
2. Klik **Aan**, kies een effen kleur. Werkt dat → de basis (bevestigd protocol) is goed.
3. **Kalibratie**: klik "Licht op" bij index 0, dan ▶ voor 1, 2, 3 … Kijk waar de LED's oplichten:
   - loopt het van boven naar beneden in de eerste sliert → `order: column`;
   - begint de tweede sliert onderaan → `serpentine: true`;
   - eerste sliert rechts → `flip_x`; index 0 onderaan → `flip_y`.
4. Opslaan. Teken iets asymmetrisch (een pijl) en controleer.

## Pixelprotocol: eerlijk verhaal

Aan/uit, kleur, helderheid, presets en statusmeldingen zijn door meerdere mensen onafhankelijk bevestigd (o.a. de HA-integratie `nzrutman/hello_fairy_ble`). Voor de **per-pixel/plaatjes-modus** is minder zekerheid: de PyPI-bibliotheek `blecontroller` zegt dat het onderstaande werkt op een 20×20 Hello Fairy-gordijn en dat formaat is hier de standaard (`pixel_encoder: indexed_da`):

```
AA DA LEN PAGE [IDX_HI IDX_LO R G B]… CS
```

Een andere capture toonde pakketten met commandobyte `0xD1`. Werkt `indexed_da` niet (LED's reageren niet of chaotisch op "Licht op"), probeer `plain_rgb`, en anders is dit de zekere weg:

1. Android-telefoon: Ontwikkelaarsopties → **Bluetooth HCI-snoop-log** aan, Bluetooth uit/aan.
2. In de Hello Fairy-app in de DIY/plaatjes-editor **één** simpel plaatje sturen (bv. één rode pixel linksboven).
3. `adb bugreport` (log zit in `FS/data/misc/bluetooth/logs/btsnoop_hci.log`) en dan
   `python tools/btsnoop_writes.py btsnoop_hci.log`
4. Je ziet exact welke bytes de app stuurt. Plak die in deze chat, dan maak ik de encoder passend — of voeg zelf een functie toe aan `PIXEL_ENCODERS` in `protocol.py`; alles daarboven (editor, API, HA) blijft gelijk.

Handig daarbij: `POST /api/raw` stuurt willekeurige bytes, dus je kunt een gesnifte write letterlijk herhalen om te zien of het gordijn hetzelfde doet.

## Beperkingen

- BLE is traag: reken op ~3–8 frames per seconde voor volledige 400-pixel frames (`max_fps` in config). Scrollende tekst is daarmee prima; vloeiende video niet.
- Eén verbinding tegelijk: de telefoon-app en deze service kunnen niet samen.
- De IR-afstandsbediening blijft werken; statuswijzigingen daarvan komen als notificatie binnen en worden doorgegeven aan HA.

## Ontwikkelen / testen

```bash
pip install -e ".[dev]"
FAIRY_TRANSPORT=fake fairycurtain        # draaien zonder hardware
pytest
```
