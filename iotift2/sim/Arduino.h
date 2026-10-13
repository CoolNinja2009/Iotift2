/*
 * Host-side stand-in for the Arduino/ESP32 core, used by tests/test_runtime.py.
 *
 * It lets the C++ that Iotift generates be compiled with the host g++ and
 * *run*, with simulated time, pins, ADC, PWM and Serial.  Every observable
 * side effect is printed as a line "[<ms>] <event>" so tests can assert on
 * behaviour, not on the text of the generated code.
 *
 * Only what generated code (and the examples' raw C blocks) actually use is
 * modelled.  Arduino.h macros that commonly clash with user identifiers
 * (PI, HIGH, DEC, ...) are defined as real macros on purpose, so a clash
 * fails here the same way it fails on the device.
 */
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <string>

typedef bool boolean;
typedef uint8_t byte;

#define ARDUINO_ARCH_ESP32 1
/* Arduino-ESP32 core major version (esp_arduino_version.h).  Builds with
   -DESP_ARDUINO_VERSION_MAJOR=3 check the core 3.x code paths. */
#ifndef ESP_ARDUINO_VERSION_MAJOR
#define ESP_ARDUINO_VERSION_MAJOR 2
#endif
#define PI 3.1415926535897932384626433832795
#define HALF_PI 1.5707963267948966192313216916398
#define TWO_PI 6.283185307179586476925286766559
#define DEG_TO_RAD 0.017453292519943295769236907684886
#define RAD_TO_DEG 57.295779513082320876798154814105
#define DEC 10
#define HEX 16
#define OCT 8
#define BIN 2
#define HIGH 1
#define LOW 0
#define INPUT 0x01
#define OUTPUT 0x03
#define INPUT_PULLUP 0x05
#define INPUT_PULLDOWN 0x09
#define RISING 0x01
#define FALLING 0x02
#define CHANGE 0x03
#define LSBFIRST 0
#define MSBFIRST 1
#define IRAM_ATTR
/* ESP32 ADC pin names */
#define A0 36
#define A3 39
#define A4 32
#define A5 33
#define A6 34
#define A7 35
#define F(x) x
#define portMAX_DELAY 0xffffffff
#define pdMS_TO_TICKS(x) (x)
typedef int gpio_num_t;

/* ── simulator state (defined in sim.cpp) ── */
extern unsigned long g_now;
extern int g_pin[64];
extern int g_analog[64];
extern int g_ledc[16];
extern int g_ledc_pin[64];
extern int g_pin_mode[64];
extern void (*g_isr[64])(void);
extern int g_isr_mode[64];

static inline unsigned long millis() { return g_now; }
static inline unsigned long micros() { return g_now * 1000UL; }
static inline void delay(unsigned long ms) { g_now += ms; }
static inline void delayMicroseconds(unsigned int) {}
static inline void yield() {}

static inline void pinMode(int p, int m) {
  g_pin_mode[p] = m;
  if (m == INPUT_PULLUP) g_pin[p] = 1;          /* idle level of a pulled-up input */
  if (m == INPUT_PULLDOWN) g_pin[p] = 0;
  printf("[%lu] pinMode(%d,%d)\n", g_now, p, m);
}
static inline void digitalWrite(int p, int v) {
  if (g_pin[p] != (v ? 1 : 0)) printf("[%lu] PIN %d -> %d\n", g_now, p, v ? 1 : 0);
  g_pin[p] = v ? 1 : 0;
}
static inline int digitalRead(int p) { return g_pin[p]; }
static inline int analogRead(int p) { return g_analog[p]; }
static inline uint32_t analogReadMilliVolts(int p) { return (uint32_t)g_analog[p] * 3300u / 4095u; }
typedef enum { ADC_0db, ADC_2_5db, ADC_6db, ADC_11db } adc_attenuation_t;
static inline void analogSetPinAttenuation(int p, adc_attenuation_t a) {
  printf("[%lu] analogSetPinAttenuation(%d,%d)\n", g_now, p, (int)a);
}
static inline void analogWrite(int p, int v) { printf("[%lu] analogWrite(%d,%d)\n", g_now, p, v); }
static inline void dacWrite(int p, int v) { printf("[%lu] dacWrite(%d,%d)\n", g_now, p, v); }
static inline int digitalPinToInterrupt(int p) { return p; }
static inline void attachInterrupt(int p, void (*f)(void), int m) {
  g_isr[p] = f; g_isr_mode[p] = m;
  printf("[%lu] attachInterrupt(%d,%d)\n", g_now, p, m);
}
static inline void detachInterrupt(int p) { g_isr[p] = 0; }
static inline void noInterrupts() {}
static inline void interrupts() {}

/*
 * LEDC (PWM).  Modelled like the real cores: channel c runs on timer
 * (c/2)%4 of group c/8, so channels c and c^1 share one timer.  Configuring
 * a timer that another attached channel already uses at a different
 * frequency prints "LEDC CLASH", the silent retune that happens on hardware.
 * The original ESP32 has 16 channels; -DIOTIFT_SIM_C3 models 6.
 */
#ifdef IOTIFT_SIM_C3
#define SIM_LEDC_CHANNELS 6
#else
#define SIM_LEDC_CHANNELS 16
#endif
extern int g_ledc_ch_pin[16];        /* GPIO + 1 attached to each channel, 0 = none */
extern uint32_t g_ledc_ch_freq[16];  /* frequency each channel actually runs at */
static inline int _sim_ledc_timer(int ch) { return (ch / 8) * 4 + (ch / 2) % 4; }
static inline void _sim_ledc_config(int ch, uint32_t f) {
  for (int o = 0; o < SIM_LEDC_CHANNELS; o++) {
    if (o == ch || _sim_ledc_timer(o) != _sim_ledc_timer(ch)) continue;
    if (g_ledc_ch_pin[o] && g_ledc_ch_freq[o] != f)
      printf("[%lu] LEDC CLASH: ch%d (GPIO %d) retuned from %u to %u Hz\n", g_now, o,
             g_ledc_ch_pin[o] - 1, g_ledc_ch_freq[o], f);
    g_ledc_ch_freq[o] = f;
  }
  g_ledc_ch_freq[ch] = f;
}
static inline int _sim_ledc_chan_of(int pin) {
  for (int c = 0; c < SIM_LEDC_CHANNELS; c++) if (g_ledc_ch_pin[c] == pin + 1) return c;
  return -1;
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
/* ESP32 core 3.x LEDC API: pin-based */
static inline bool ledcAttachChannel(uint8_t pin, uint32_t f, uint8_t bits, uint8_t ch) {
  if (_sim_ledc_chan_of(pin) >= 0) {
    printf("[%lu] E ledcAttachChannel: pin %d already attached\n", g_now, pin);
    return false;
  }
  if (ch >= SIM_LEDC_CHANNELS) { printf("[%lu] E ledcAttachChannel: no channel %d\n", g_now, ch); return false; }
  bool used = false;
  for (int p = 0; p < 64; p++) used = used || (p != pin && _sim_ledc_chan_of(p) == ch);
  printf("[%lu] ledcAttachChannel(%d,%u,%d,ch%d)\n", g_now, pin, f, bits, ch);
  if (!used) _sim_ledc_config(ch, f);
  g_ledc_ch_pin[ch] = pin + 1;
  return true;
}
static inline bool ledcAttach(uint8_t pin, uint32_t f, uint8_t bits) {   /* lowest free channel */
  for (int c = 0; c < SIM_LEDC_CHANNELS; c++)
    if (!g_ledc_ch_pin[c]) return ledcAttachChannel(pin, f, bits, c);
  return false;
}
static inline uint32_t ledcChangeFrequency(uint8_t pin, uint32_t f, uint8_t bits) {
  int c = _sim_ledc_chan_of(pin);
  if (c < 0) return 0;
  printf("[%lu] ledcChangeFrequency(%d,%u,%d)\n", g_now, pin, f, bits);
  _sim_ledc_config(c, f);
  return f;
}
static inline bool ledcWrite(uint8_t pin, uint32_t d) {
  if (_sim_ledc_chan_of(pin) < 0) { printf("[%lu] E ledcWrite: pin %d not attached\n", g_now, pin); return false; }
  if (g_ledc_pin[pin] != (int)d) printf("[%lu] ledcWrite(pin%d,%u)\n", g_now, pin, d);
  g_ledc_pin[pin] = (int)d;
  return true;
}
static inline bool ledcDetach(uint8_t pin) {
  int c = _sim_ledc_chan_of(pin);
  if (c < 0) return false;
  printf("[%lu] ledcDetach(%d)\n", g_now, pin);
  g_ledc_ch_pin[c] = 0;
  return true;
}
#else
/* ESP32 core 2.x LEDC API: channel-based */
static inline uint32_t ledcSetup(uint8_t ch, uint32_t f, uint8_t bits) {
  if (ch >= SIM_LEDC_CHANNELS) { printf("[%lu] E ledcSetup: no channel %d\n", g_now, ch); return 0; }
  printf("[%lu] ledcSetup(ch%d,%u,%d)\n", g_now, ch, f, bits);
  _sim_ledc_config(ch, f);
  return f;
}
static inline void ledcAttachPin(uint8_t pin, uint8_t ch) {
  printf("[%lu] ledcAttachPin(%d,ch%d)\n", g_now, pin, ch);
  g_ledc_ch_pin[ch] = pin + 1;
}
static inline void ledcDetachPin(uint8_t pin) {
  printf("[%lu] ledcDetachPin(%d)\n", g_now, pin);
  int c = _sim_ledc_chan_of(pin);
  if (c >= 0) g_ledc_ch_pin[c] = 0;
}
static inline void ledcWrite(uint8_t ch, uint32_t d) {
  if (g_ledc[ch] != (int)d) printf("[%lu] ledcWrite(ch%d,%u)\n", g_now, ch, d);
  g_ledc[ch] = (int)d;
}
#endif

static inline long random(long hi) { return hi ? rand() % hi : 0; }
static inline long random(long lo, long hi) { return lo + random(hi - lo); }
static inline long map(long x, long a, long b, long c, long d) { return (x - a) * (d - c) / (b - a) + c; }

struct EspClass {
  void restart() { printf("[%lu] ESP.restart\n", g_now); exit(0); }
};
static EspClass ESP;

class String : public std::string {
 public:
  String() {}
  String(const char *s) : std::string(s ? s : "") {}
  String(const std::string &s) : std::string(s) {}
  String(int v) : std::string(std::to_string(v)) {}
  String(unsigned v) : std::string(std::to_string(v)) {}
  String(long v) : std::string(std::to_string(v)) {}
  String(unsigned long v) : std::string(std::to_string(v)) {}
  String(double v, int digits = 2) { char b[64]; snprintf(b, 64, "%.*f", digits, v); assign(b); }
  String(float v, int digits = 2) : String((double)v, digits) {}
  String(char c) : std::string(1, c) {}
  int toInt() const { return atoi(c_str()); }
  float toFloat() const { return (float)atof(c_str()); }
  String substring(size_t a) const { return a < size() ? String(substr(a)) : String(); }
  String substring(size_t a, size_t b) const { return a < size() ? String(substr(a, b - a)) : String(); }
  bool startsWith(const String &p) const { return compare(0, p.size(), p) == 0; }
  int indexOf(char c) const { size_t i = find(c); return i == npos ? -1 : (int)i; }
  void trim() {
    size_t a = find_first_not_of(" \r\n\t"), b = find_last_not_of(" \r\n\t");
    if (a == npos) { clear(); return; }
    assign(substr(a, b - a + 1));
  }
};

/* Serial: buffers a line and prints it as "[ms] SERIAL: <text>" on newline.
   Input queued by "<ms>:RX:<text>" events is returned by available()/read(). */
struct HardwareSerial {
  std::string line, rx;
  int begun = 0;
  void begin(unsigned long baud) {
    if (begun++) ::printf("[%lu] Serial.begin(%lu)\n", g_now, baud);
  }
  void out(const String &s) {
    for (char c : s) {
      if (c == '\n') nl();
      else if (c != '\r') line += c;
    }
  }
  void nl();
  template <class T> void print(T v) { out(String(v)); }
  void print(const char *s) { out(String(s)); }
  void print(const String &s) { out(s); }
  void print(double v, int d) { out(String(v, d)); }
  void print(bool b) { out(String((int)b)); }
  template <class T> void println(T v) { print(v); nl(); }
  void println(double v, int d) { print(v, d); nl(); }
  void println() { nl(); }
  void printf(const char *fmt, ...);
  int available() { return (int)rx.size(); }
  int read() {
    if (rx.empty()) return -1;
    int c = (unsigned char)rx[0];
    rx.erase(0, 1);
    return c;
  }
  String readStringUntil(char) { return String(); }
  void flush() {}
  operator bool() { return true; }
};
extern HardwareSerial Serial;
