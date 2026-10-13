/*
 * Host-side stand-in for the Arduino-ESP32 SPI library.  The simulated
 * device is a shift register: each transfer returns the previous byte sent.
 */
#pragma once
#include "Arduino.h"
#define SPI_MODE0 0
#define SPI_MODE1 1
#define SPI_MODE2 2
#define SPI_MODE3 3
#define SPI_LSBFIRST 0
#define SPI_MSBFIRST 1

class SPISettings {
 public:
  uint32_t clock; uint8_t order, mode;
  SPISettings() : clock(1000000), order(SPI_MSBFIRST), mode(SPI_MODE0) {}
  SPISettings(uint32_t c, uint8_t o, uint8_t m) : clock(c), order(o), mode(m) {}
};
class SPIClass {
 public:
  uint8_t last = 0;
  bool in_tx = false;
  void begin(int8_t = -1, int8_t = -1, int8_t = -1, int8_t = -1) { printf("[%lu] SPI.begin\n", g_now); }
  void beginTransaction(SPISettings s) {
    in_tx = true;
    printf("[%lu] SPI tx %luHz mode%d\n", g_now, (unsigned long)s.clock, s.mode);
  }
  uint8_t transfer(uint8_t b) {
    uint8_t r = last;
    last = b;
    printf("[%lu] SPI %02X -> %02X%s\n", g_now, b, r, in_tx ? "" : " (outside transaction)");
    return r;
  }
  void endTransaction() { in_tx = false; printf("[%lu] SPI end\n", g_now); }
};
extern SPIClass SPI;
