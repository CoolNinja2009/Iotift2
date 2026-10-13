// Host stand-in for the Adafruit BME280 library: answers at 0x76/0x77 with readings that drift with time.
#pragma once
#include <Arduino.h>
#include <Wire.h>

class Adafruit_BME280 {
public:
  bool begin(uint8_t addr = 0x77, TwoWire *wire = &Wire) {
    (void)wire;
    ok_ = addr == 0x76 || addr == 0x77;
    printf("[%lu] BME280 begin 0x%02X %s\n", g_now, addr, ok_ ? "ok" : "missing");
    return ok_;
  }
  float readTemperature() { return 21.0f + (float)(g_now % 10000) / 2000.0f; }   // 21.0 .. 26.0 C
  float readHumidity() { return 40.0f + (float)(g_now % 6000) / 1000.0f; }       // 40 .. 46 %
  float readPressure() { return 101325.0f - (float)(g_now % 4000); }              // Pa
private:
  bool ok_ = false;
};
