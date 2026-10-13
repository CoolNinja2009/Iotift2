// Host stand-in for the Adafruit SSD1306 + GFX libraries: collects what is drawn and prints
// one line per display() call, with " | " where the cursor moved.
#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <string>

#define SSD1306_SWITCHCAPVCC 0x02
#define SSD1306_EXTERNALVCC 0x01
#define SSD1306_BLACK 0
#define SSD1306_WHITE 1

class Adafruit_SSD1306 {
public:
  Adafruit_SSD1306(uint8_t w, uint8_t h, TwoWire *twi = &Wire, int8_t rst = -1) : w_(w), h_(h) { (void)twi; (void)rst; }
  bool begin(uint8_t vcc = SSD1306_SWITCHCAPVCC, uint8_t addr = 0, bool reset = true, bool periph = true) {
    (void)vcc; (void)reset; (void)periph;
    printf("[%lu] OLED begin %ux%u at 0x%02X\n", g_now, w_, h_, addr);
    return addr == 0x3C || addr == 0x3D;
  }
  void clearDisplay() { buf_.clear(); }
  void setTextSize(uint8_t) {}
  void setTextColor(uint16_t) {}
  void setCursor(int16_t, int16_t) { if (!buf_.empty()) buf_ += " | "; }
  size_t print(const char *s) { buf_ += s; return strlen(s); }
  size_t print(float v, int digits = 2) { char t[32]; snprintf(t, sizeof t, "%.*f", digits, v); buf_ += t; return strlen(t); }
  size_t print(int v) { buf_ += std::to_string(v); return 1; }
  void drawRect(int16_t, int16_t, int16_t, int16_t, uint16_t) {}
  void fillRect(int16_t, int16_t, int16_t, int16_t, uint16_t) {}
  void display() { printf("[%lu] OLED %s\n", g_now, buf_.c_str()); }
private:
  uint8_t w_, h_;
  std::string buf_;
};
