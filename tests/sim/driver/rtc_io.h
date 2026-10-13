/* Host-side stand-in for ESP-IDF driver/rtc_io.h (original ESP32 RTC GPIOs). */
#pragma once
#include "Arduino.h"
#include "esp_err.h"
static inline bool rtc_gpio_is_valid_gpio(gpio_num_t p) {
  static const int rtc[] = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33, 34, 35, 36, 37, 38, 39};
  for (int r : rtc) if (r == (int)p) return true;
  return false;
}
static inline esp_err_t rtc_gpio_pullup_en(gpio_num_t p) { printf("[%lu] rtc pullup %d\n", g_now, (int)p); return ESP_OK; }
static inline esp_err_t rtc_gpio_pullup_dis(gpio_num_t) { return ESP_OK; }
static inline esp_err_t rtc_gpio_pulldown_en(gpio_num_t p) { printf("[%lu] rtc pulldown %d\n", g_now, (int)p); return ESP_OK; }
static inline esp_err_t rtc_gpio_pulldown_dis(gpio_num_t) { return ESP_OK; }
