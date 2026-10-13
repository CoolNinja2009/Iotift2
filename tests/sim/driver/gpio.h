/* Host-side stand-in for ESP-IDF driver/gpio.h (wake-up subset). */
#pragma once
#include "Arduino.h"
#include "esp_err.h"
typedef enum { GPIO_INTR_DISABLE = 0, GPIO_INTR_LOW_LEVEL = 4, GPIO_INTR_HIGH_LEVEL = 5 } gpio_int_type_t;
static inline esp_err_t gpio_wakeup_enable(gpio_num_t pin, gpio_int_type_t t) {
  printf("[%lu] gpio_wakeup_enable(%d,%s)\n", g_now, (int)pin, t == GPIO_INTR_HIGH_LEVEL ? "high" : "low");
  return ESP_OK;
}
