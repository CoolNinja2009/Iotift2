// Host stand-in for the Adafruit NeoPixel library: same signatures, prints instead of driving LEDs.
#pragma once
#include <Arduino.h>

typedef uint16_t neoPixelType;
#define NEO_GRB ((1 << 6) | (1 << 4) | (0 << 2) | (2))
#define NEO_KHZ800 0x0000

class Adafruit_NeoPixel {
public:
  Adafruit_NeoPixel(uint16_t n, int16_t pin = 6, neoPixelType type = NEO_GRB + NEO_KHZ800)
      : n_(n), pin_(pin) { (void)type; }
  void begin(void) { printf("[%lu] NEOPIXEL begin pin %d\n", g_now, pin_); }
  void setBrightness(uint8_t b) { printf("[%lu] NEOPIXEL brightness %u\n", g_now, b); }
  void setPixelColor(uint16_t i, uint32_t c) { if (i < 8) px_[i] = c; }
  void setPixelColor(uint16_t i, uint8_t r, uint8_t g, uint8_t b) { setPixelColor(i, ((uint32_t)r << 16) | ((uint32_t)g << 8) | b); }
  void show(void) {
    printf("[%lu] NEOPIXEL", g_now);
    for (uint16_t i = 0; i < n_ && i < 8; i++) printf(" %06lx", (unsigned long)px_[i]);
    printf("\n");
  }
private:
  uint16_t n_;
  int16_t pin_;
  uint32_t px_[8] = {};
};
