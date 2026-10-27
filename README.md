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
| `POST /text` | tekst in pagina's | `{"text":"Hoi","color":"#00ffff","page_seconds":4,"movement":"loop_left"}` |
| `POST /movement` | plaatje bewegen/knipperen/ademen | `{"direction":"loop_left","speed":40,"brightness":80}` |
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

## Pixelprotocol (bevestigd)

Het per-pixel protocol komt uit een Bluetooth-capture van de officiële app plus de APK, uitgezocht door **kmich** (fork van `nzrutman/hello_fairy_ble`, branch `curtain-studio-rc5`, `PROTOCOL.md`) en fysiek bevestigd op een 20×20-gordijn. Bijzonderheden die je merkt in de praktijk:

- Het gordijn kent per pixel alleen een **tint** (0–180 = hue/2), plus **wit** (255) en **uit** (254). Geen helderheid of pastel per pixel; de editor laat donkere kleuren "uit" worden en grijzige kleuren wit. Helderheid is globaal (bij "Beweging").
- Een upload is precies zes pakketten (5×242 + 20 bytes) met vaste tussenpozen; totaal ≈ 2,5 s. Daarom is een frame-animatie traag (`max_fps: 0.4`). Beweging, knipperen en ademen van een plaatje doet het gordijn zelf (`POST /api/movement`), zonder nieuwe upload.
- Index = kolom × 20 + rij (slierten van boven naar beneden): de standaard-layout. Het gordijn meldt zijn afmetingen zelf (`info` in `/api/status`).

De oude gissingen staan er nog als `legacy_*`-encoders; het gordijn negeert ze. `tools/btsnoop_writes.py` blijft handig om zelf nieuwe commando's te ontdekken (muziekmodus, timers, twee-plaatjes-animatie staan in kmich's `PROTOCOL.md` beschreven en zijn een logische volgende uitbreiding).

## Beperkingen

- Een volledig plaatje uploaden kost ≈ 2,5 s. Tekst gaat daarom in pagina's van ~3 tekens; GIF's worden een diashow. Voor beweging gebruik je de ingebouwde bewegingen van het gordijn.
- Eén tint per pixel: foto's worden "posterized". Pixel-art, iconen en tekst zien er goed uit.
- Eén verbinding tegelijk: de telefoon-app en deze service kunnen niet samen.
- De IR-afstandsbediening blijft werken; statuswijzigingen daarvan komen als notificatie binnen en worden doorgegeven aan HA.

## Ontwikkelen / testen

```bash
pip install -e ".[dev]"
FAIRY_TRANSPORT=fake fairycurtain        # draaien zonder hardware
pytest
```
