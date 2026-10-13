// A stand-in for a C++ LED strip library, used to test `extern cpp`.
#pragma once
#include <Arduino.h>

#define FAKE_STRIP_MAX 64
#define FAKE_STRIP_VERSION 3

class FakeStrip {
public:
    FakeStrip(uint16_t count, uint8_t pin) : count_(count), pin_(pin) {}
    void begin() { Serial.print("strip begin "); Serial.println(count_); }
    void setPixel(uint16_t i, uint32_t rgb) { if (i < FAKE_STRIP_MAX) px_[i] = rgb; }
    uint32_t getPixel(uint16_t i) const { return i < FAKE_STRIP_MAX ? px_[i] : 0; }
    void show() { shows_++; Serial.print("show "); Serial.println(shows_); }
    uint16_t count() const { return count_; }
    void slowFlush() { delay(50); }
private:
    uint16_t count_;
    uint8_t pin_;
    uint32_t px_[FAKE_STRIP_MAX] = {};
    uint32_t shows_ = 0;
};

inline uint32_t fake_rgb(uint8_t r, uint8_t g, uint8_t b) {
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}
inline void fake_reset_bus() { delay(200); }
