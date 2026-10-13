/*
 * Host-side stand-in for the ESP32 Arduino WiFi library (core 2.x/3.x API).
 * Driver events are injected by sim.cpp (see "W:" events there).
 */
#pragma once
#include "Arduino.h"

typedef enum { WIFI_MODE_NULL = 0, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3 } wifi_mode_t;
typedef enum { WIFI_PS_NONE = 0, WIFI_PS_MIN_MODEM = 1, WIFI_PS_MAX_MODEM = 2 } wifi_ps_type_t;
#define WIFI_SCAN_RUNNING (-1)
#define WIFI_SCAN_FAILED (-2)

typedef enum {
  ARDUINO_EVENT_WIFI_SCAN_DONE = 1,
  ARDUINO_EVENT_WIFI_STA_CONNECTED = 4,
  ARDUINO_EVENT_WIFI_STA_DISCONNECTED = 5,
  ARDUINO_EVENT_WIFI_STA_GOT_IP = 7,
  ARDUINO_EVENT_WIFI_AP_STACONNECTED = 12,
  ARDUINO_EVENT_WIFI_AP_STADISCONNECTED = 13,
} arduino_event_id_t;
typedef arduino_event_id_t WiFiEvent_t;
typedef union { int unused; } arduino_event_info_t;
typedef arduino_event_info_t WiFiEventInfo_t;
typedef void (*WiFiEventFuncCb)(arduino_event_id_t, arduino_event_info_t);

struct IPAddress {
  uint8_t b[4];
  IPAddress(int a = 0, int c = 0, int d = 0, int e = 0) { b[0] = a; b[1] = c; b[2] = d; b[3] = e; }
  String toString() const { char s[20]; snprintf(s, 20, "%d.%d.%d.%d", b[0], b[1], b[2], b[3]); return s; }
};

struct WiFiClass {
  WiFiEventFuncCb cb = 0;
  int scan_state = WIFI_SCAN_FAILED;
  int ap_clients = 0;

  void mode(int m) { printf("[%lu] WiFi.mode(%d)\n", g_now, m); }
  void begin(const char *ssid, const char * = 0) { printf("[%lu] WiFi.begin(%s)\n", g_now, ssid); }
  bool disconnect(bool = false, bool = false) { printf("[%lu] WiFi.disconnect\n", g_now); return true; }
  bool softAPdisconnect(bool = false) { return true; }
  bool setHostname(const char *h) { printf("[%lu] WiFi.setHostname(%s)\n", g_now, h); return true; }
  bool setSleep(bool) { return true; }
  bool setSleep(wifi_ps_type_t) { return true; }
  bool setAutoReconnect(bool) { return true; }
  static void persistent(bool) {}
  bool config(IPAddress ip, IPAddress, IPAddress, IPAddress = IPAddress()) {
    printf("[%lu] WiFi.config(%s)\n", g_now, ip.toString().c_str());
    return true;
  }
  bool softAP(const char *s, const char *p = 0, int ch = 1, int = 0, int max = 4) {
    printf("[%lu] WiFi.softAP(%s,%s,ch%d,max%d)\n", g_now, s, p ? p : "(open)", ch, max);
    return true;
  }
  IPAddress softAPIP() { return IPAddress(192, 168, 4, 1); }
  IPAddress localIP() { return IPAddress(192, 168, 1, 50); }
  String macAddress() { return "AA:BB:CC:DD:EE:FF"; }
  String softAPmacAddress() { return "AA:BB:CC:DD:EE:F0"; }
  int scanNetworks(bool async = false, bool = false) {
    printf("[%lu] WiFi.scanNetworks(async=%d)\n", g_now, async);
    scan_state = WIFI_SCAN_RUNNING;
    return WIFI_SCAN_RUNNING;
  }
  int scanComplete() { return scan_state; }
  void scanDelete() { if (scan_state >= 0) scan_state = WIFI_SCAN_FAILED; }
  String SSID(int i = 0) { return i ? "NetB" : "NetA"; }
  int32_t RSSI(int i = 0) { return -50 - i * 10; }
  int32_t channel(int = 0) { return 6; }
  int softAPgetStationNum() { return ap_clients; }
  void onEvent(WiFiEventFuncCb f) { cb = f; }
  void fire(arduino_event_id_t e) { if (cb) { arduino_event_info_t i{}; cb(e, i); } }
};
extern WiFiClass WiFi;
