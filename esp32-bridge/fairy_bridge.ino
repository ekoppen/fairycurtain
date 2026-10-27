/*
 * fairy_bridge – ESP32 als domme BLE↔HTTP-brug voor FairyCurtain.
 *
 * De Python-service (transport: esp32) doet al het denkwerk; deze ESP32
 * verbindt alleen met het Hello Fairy-gordijn en schrijft de bytes die hij
 * via HTTP krijgt 1-op-1 naar de GATT-characteristic. Notificaties van het
 * gordijn worden in een wachtrij bewaard en via GET /notify opgehaald.
 *
 * Bibliotheken (Arduino IDE / PlatformIO):
 *   - NimBLE-Arduino (h2zero)  >= 1.4
 *   - ESP32 core (WiFi, WebServer, ESPmDNS zitten erin)
 *
 * Endpoints:
 *   GET  /status            {"connected":bool,"mtu":n,"mac":"..","rssi":n}
 *   POST /connect?mac=..    verbinden (zonder mac: scant op naam "Hello Fairy-")
 *   POST /disconnect
 *   POST /write             body = hex-string (bv. aa020101ae)
 *   GET  /notify            {"queue":["aa0200ac",...]}  (leegt de wachtrij)
 */
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <NimBLEDevice.h>

// ------------------------------------------------------------ instellingen
const char* WIFI_SSID = "JOUW-WIFI";
const char* WIFI_PASS = "JOUW-WACHTWOORD";
const char* MDNS_NAME = "fairy-bridge";          // http://fairy-bridge.local
const char* NAME_PREFIX = "Hello Fairy-";

static const NimBLEUUID SVC_UUID("49535343-fe7d-4ae5-8fa9-9fafd205e455");
static const NimBLEUUID WRITE_UUID("49535343-8841-43f4-a8d4-ecbe34729bb3");
static const NimBLEUUID NOTIFY_UUID("49535343-1e4d-4bd9-ba61-23c647249616");

// ------------------------------------------------------------------ state
WebServer server(80);
NimBLEClient* client = nullptr;
NimBLERemoteCharacteristic* writeChar = nullptr;
String connectedMac = "";
String notifyQueue[32];
volatile int qHead = 0, qTail = 0;

static String toHex(const uint8_t* d, size_t n) {
  static const char* h = "0123456789abcdef";
  String s; s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) { s += h[d[i] >> 4]; s += h[d[i] & 15]; }
  return s;
}

static void onNotify(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  int next = (qTail + 1) % 32;
  if (next == qHead) return;            // vol: oudste laten staan
  notifyQueue[qTail] = toHex(data, len);
  qTail = next;
}

class ClientCb : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient*, int) override { writeChar = nullptr; connectedMac = ""; }
};

// ------------------------------------------------------------------- BLE
static String scanForCurtain() {
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setActiveScan(true);
  NimBLEScanResults res = scan->getResults(8000, false);
  for (int i = 0; i < res.getCount(); i++) {
    const NimBLEAdvertisedDevice* d = res.getDevice(i);
    if (d->haveName() && String(d->getName().c_str()).startsWith(NAME_PREFIX))
      return String(d->getAddress().toString().c_str());
  }
  return "";
}

static bool connectCurtain(String mac) {
  if (mac.length() == 0) mac = scanForCurtain();
  if (mac.length() == 0) return false;
  if (!client) { client = NimBLEDevice::createClient(); client->setClientCallbacks(new ClientCb(), true); }
  client->setConnectTimeout(10000);
  if (!client->connect(NimBLEAddress(mac.c_str(), BLE_ADDR_PUBLIC))) return false;
  NimBLERemoteService* svc = client->getService(SVC_UUID);
  if (!svc) { client->disconnect(); return false; }
  writeChar = svc->getCharacteristic(WRITE_UUID);
  NimBLERemoteCharacteristic* n = svc->getCharacteristic(NOTIFY_UUID);
  if (n && n->canNotify()) n->subscribe(true, onNotify);
  connectedMac = mac;
  return writeChar != nullptr;
}

static bool isConnected() { return client && client->isConnected() && writeChar; }

// ------------------------------------------------------------------ HTTP
static void sendJson(const String& s, int code = 200) { server.send(code, "application/json", s); }

static void hStatus() {
  String s = "{\"connected\":" + String(isConnected() ? "true" : "false");
  s += ",\"mtu\":" + String(isConnected() ? client->getMTU() : 23);
  s += ",\"mac\":\"" + connectedMac + "\"";
  s += ",\"rssi\":" + String(isConnected() ? client->getRssi() : 0) + "}";
  sendJson(s);
}
static void hConnect() {
  String mac = server.hasArg("mac") ? server.arg("mac") : "";
  if (isConnected() && (mac.length() == 0 || mac.equalsIgnoreCase(connectedMac))) { hStatus(); return; }
  if (isConnected()) client->disconnect();
  bool ok = connectCurtain(mac);
  if (!ok) { sendJson("{\"connected\":false,\"error\":\"verbinden mislukt\"}", 503); return; }
  hStatus();
}
static void hDisconnect() { if (client) client->disconnect(); writeChar = nullptr; connectedMac = ""; hStatus(); }
static void hWrite() {
  if (!isConnected()) { sendJson("{\"error\":\"niet verbonden\"}", 503); return; }
  String hex = server.arg("plain"); hex.trim();
  size_t n = hex.length() / 2; uint8_t buf[256];
  if (n == 0 || n > sizeof(buf)) { sendJson("{\"error\":\"lengte\"}", 400); return; }
  for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)strtoul(hex.substring(i * 2, i * 2 + 2).c_str(), nullptr, 16);
  bool ok = writeChar->writeValue(buf, n, false);
  sendJson(String("{\"ok\":") + (ok ? "true" : "false") + ",\"len\":" + n + "}", ok ? 200 : 500);
}
static void hNotify() {
  String s = "{\"queue\":[";
  bool first = true;
  while (qHead != qTail) {
    if (!first) s += ",";
    s += "\"" + notifyQueue[qHead] + "\""; first = false;
    qHead = (qHead + 1) % 32;
  }
  s += "]}";
  sendJson(s);
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print("."); }
  Serial.printf("\nWiFi: %s\n", WiFi.localIP().toString().c_str());
  MDNS.begin(MDNS_NAME);

  NimBLEDevice::init("fairy-bridge");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  NimBLEDevice::setMTU(247);

  server.on("/status", HTTP_GET, hStatus);
  server.on("/connect", HTTP_POST, hConnect);
  server.on("/disconnect", HTTP_POST, hDisconnect);
  server.on("/write", HTTP_POST, hWrite);
  server.on("/notify", HTTP_GET, hNotify);
  server.begin();
}

void loop() { server.handleClient(); delay(1); }
