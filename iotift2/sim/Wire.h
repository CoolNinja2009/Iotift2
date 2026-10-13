/*
 * Host-side stand-in for the Arduino-ESP32 Wire library.
 * Two simulated register devices answer, at 0x48 and 0x68 (where an MPU6050
 * would be): the first byte of a write sets the device's register pointer,
 * further bytes are written there with auto-increment, and reads return
 * registers from the pointer on.  Registers start out as (n * 3) & 0xFF.
 * Every transfer is printed.
 */
#pragma once
#include "Arduino.h"
#include <vector>

#define SIM_I2C_ADDR 0x48
#define SIM_I2C_ADDR2 0x68

class TwoWire {
 public:
  uint8_t mem[2][256];
  uint8_t ptrs[2] = {0, 0};
  int addr = -1;
  std::vector<uint8_t> tx, rx;
  TwoWire() { for (int d = 0; d < 2; d++) for (int i = 0; i < 256; i++) mem[d][i] = (uint8_t)(i * 3); }
  static int dev(int a) { return a == SIM_I2C_ADDR ? 0 : a == SIM_I2C_ADDR2 ? 1 : -1; }
  bool begin(int sda, int scl, uint32_t freq = 0) {
    printf("[%lu] Wire.begin(sda=%d,scl=%d,%lu)\n", g_now, sda, scl, (unsigned long)freq);
    return true;
  }
  void beginTransmission(uint8_t a) { addr = a; tx.clear(); }
  size_t write(uint8_t b) { tx.push_back(b); return 1; }
  uint8_t endTransmission(bool stop = true) {
    int d = dev(addr);
    if (d < 0) return 2;                           /* address NACK */
    uint8_t *regs = mem[d];
    uint8_t &ptr = ptrs[d];
    if (!tx.empty()) {
      printf("[%lu] I2C W %02X:", g_now, addr);
      for (uint8_t b : tx) printf(" %02X", b);
      printf("%s\n", stop ? "" : " (no stop)");
      ptr = tx[0];
      for (size_t i = 1; i < tx.size(); i++) regs[ptr++] = tx[i];
    }
    return 0;
  }
  size_t requestFrom(uint8_t a, size_t n, bool) {
    rx.clear();
    int d = dev(a);
    if (d < 0) return 0;
    uint8_t *regs = mem[d];
    uint8_t &ptr = ptrs[d];
    for (size_t i = 0; i < n; i++) rx.push_back(regs[ptr++]);
    printf("[%lu] I2C R %02X:", g_now, a);
    for (uint8_t b : rx) printf(" %02X", b);
    printf("\n");
    return n;
  }
  int available() { return (int)rx.size(); }
  int read() {
    if (rx.empty()) return -1;
    int b = rx.front();
    rx.erase(rx.begin());
    return b;
  }
};
extern TwoWire Wire;
