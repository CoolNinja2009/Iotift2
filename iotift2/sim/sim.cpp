/*
 * Simulation driver: links against generated code (setup()/loop()) and runs
 * it with a virtual millisecond clock.
 *
 *   ./program <duration_ms> [events]
 *
 * events is a comma-separated list of "<ms>:<what>":
 *   <ms>:<gpio>:<0|1>      drive a digital input
 *   <ms>:A<gpio>:<value>   set an ADC reading
 *   <ms>:W:got_ip          station connected and got an IP
 *   <ms>:W:lost            station disconnected
 *   <ms>:W:scan_done       pending async scan finishes (2 networks)
 *   <ms>:W:join / W:leave  a client joins / leaves the access point
 *   <ms>:RX:<text>         bytes arrive on Serial (no spaces or commas)
 *   0:WAKE:<cause>         boot wake-up cause: timer, ext0 or gpio
 *
 * Each loop() call advances the clock by 1 ms (unless the code called delay()).
 */
#include <stdarg.h>
#include "Arduino.h"
#include "WiFi.h"
#include "Wire.h"
#include "SPI.h"
#include "esp_sleep.h"
#include "esp_task_wdt.h"

unsigned long g_now = 0;
int g_pin[64];
int g_analog[64];
int g_ledc[16];
int g_ledc_pin[64];
int g_ledc_ch_pin[16];
uint32_t g_ledc_ch_freq[16];
int g_pin_mode[64];
void (*g_isr[64])(void);
int g_isr_mode[64];
HardwareSerial Serial;
WiFiClass WiFi;
TwoWire Wire;
SPIClass SPI;
uint64_t g_sleep_timer_us = 0;
int g_sleep_pin = -1, g_sleep_level = -1;
int g_wake_cause = ESP_SLEEP_WAKEUP_UNDEFINED;
unsigned long g_wdt_timeout_ms = 0, g_wdt_last = 0;
bool g_wdt_on = false;

void HardwareSerial::nl() {
  ::printf("[%lu] SERIAL: %s\n", g_now, line.c_str());
  line.clear();
}
void HardwareSerial::printf(const char *fmt, ...) {
  char b[512];
  va_list a;
  va_start(a, fmt);
  vsnprintf(b, sizeof b, fmt, a);
  va_end(a);
  out(b);
}

extern void setup();
extern void loop();

struct Ev { unsigned long t; int pin; int val; bool analog; char wifi[16]; char rx[64]; };

static void apply(const Ev &e) {
  if (e.rx[0]) {
    printf("[%lu] <rx %s>\n", g_now, e.rx);
    Serial.rx += e.rx;
    return;
  }
  if (e.wifi[0]) {
    printf("[%lu] <wifi %s>\n", g_now, e.wifi);
    if (!strcmp(e.wifi, "got_ip")) WiFi.fire(ARDUINO_EVENT_WIFI_STA_GOT_IP);
    else if (!strcmp(e.wifi, "lost")) WiFi.fire(ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
    else if (!strcmp(e.wifi, "scan_done")) WiFi.scan_state = 2;
    else if (!strcmp(e.wifi, "join")) { WiFi.ap_clients++; WiFi.fire(ARDUINO_EVENT_WIFI_AP_STACONNECTED); }
    else if (!strcmp(e.wifi, "leave")) { WiFi.ap_clients--; WiFi.fire(ARDUINO_EVENT_WIFI_AP_STADISCONNECTED); }
    return;
  }
  if (e.analog) {
    g_analog[e.pin] = e.val;
    printf("[%lu] <analog %d = %d>\n", g_now, e.pin, e.val);
    return;
  }
  int old = g_pin[e.pin];
  g_pin[e.pin] = e.val;
  printf("[%lu] <input %d = %d>\n", g_now, e.pin, e.val);
  void (*isr)(void) = g_isr[e.pin];
  int m = g_isr_mode[e.pin];
  if (isr && ((m == RISING && !old && e.val) || (m == FALLING && old && !e.val) ||
              (m == CHANGE && old != e.val)))
    isr();
}

int main(int argc, char **argv) {
  unsigned long duration = argc > 1 ? strtoul(argv[1], 0, 10) : 1000;
  static Ev ev[256];
  int n = 0;
  for (int i = 0; i < 16; i++) g_ledc[i] = -1;
  if (argc > 2) {
    char *spec = strdup(argv[2]);
    for (char *t = strtok(spec, ","); t && n < 256; t = strtok(0, ",")) {
      Ev e = {};
      char what[16] = {0};
      if (sscanf(t, "%lu:RX:%63s", &e.t, e.rx) == 2) { ev[n++] = e; continue; }
      if (sscanf(t, "%lu:WAKE:%15s", &e.t, what) == 2) {
        g_wake_cause = !strcmp(what, "timer") ? ESP_SLEEP_WAKEUP_TIMER
                     : !strcmp(what, "ext0")  ? ESP_SLEEP_WAKEUP_EXT0
                     : !strcmp(what, "gpio")  ? ESP_SLEEP_WAKEUP_GPIO : ESP_SLEEP_WAKEUP_UNDEFINED;
        continue;
      }
      if (sscanf(t, "%lu:W:%15s", &e.t, e.wifi) == 2) { ev[n++] = e; continue; }
      e.wifi[0] = 0;
      if (sscanf(t, "%lu:%15[^:]:%d", &e.t, what, &e.val) != 3) {
        fprintf(stderr, "bad event '%s'\n", t);
        return 2;
      }
      e.analog = what[0] == 'A';
      e.pin = atoi(what + (e.analog ? 1 : 0));
      ev[n++] = e;
    }
  }
  setup();
  bool first = true;
  unsigned long prev = 0;
  while (g_now < duration) {
    /* Apply every event that became due since the previous iteration
       (several ms may pass at once if the program called delay()). */
    for (int i = 0; i < n; i++)
      if (ev[i].t <= g_now && (first || ev[i].t > prev)) apply(ev[i]);
    first = false;
    prev = g_now;
    unsigned long before = g_now;
    loop();
    if (g_now == before) g_now++;
    if (g_wdt_on && g_wdt_timeout_ms && g_now - g_wdt_last > g_wdt_timeout_ms) {
      printf("[%lu] WDT RESET (not fed for %lums)\n", g_now, g_now - g_wdt_last);
      break;
    }
  }
  fflush(stdout);
  return 0;
}
