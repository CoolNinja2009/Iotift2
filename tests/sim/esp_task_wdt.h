/*
 * Host-side stand-in for the ESP-IDF task watchdog (IDF 4.4 API for core 2.x,
 * IDF 5.x API for core 3.x).  If a subscribed task is not fed within the
 * timeout, sim.cpp prints "WDT RESET" and ends the run, as the chip would
 * panic and reboot.
 */
#pragma once
#include "Arduino.h"
#include "esp_err.h"
typedef void *TaskHandle_t;
extern unsigned long g_wdt_timeout_ms, g_wdt_last;
extern bool g_wdt_on;

#if ESP_ARDUINO_VERSION_MAJOR >= 3
typedef struct { uint32_t timeout_ms; uint32_t idle_core_mask; bool trigger_panic; } esp_task_wdt_config_t;
static inline esp_err_t esp_task_wdt_init(const esp_task_wdt_config_t *c) {
  g_wdt_timeout_ms = c->timeout_ms; return ESP_OK;
}
static inline esp_err_t esp_task_wdt_reconfigure(const esp_task_wdt_config_t *c) {
  g_wdt_timeout_ms = c->timeout_ms;
  printf("[%lu] wdt timeout %lums\n", g_now, g_wdt_timeout_ms);
  return ESP_OK;
}
#else
static inline esp_err_t esp_task_wdt_init(uint32_t timeout_s, bool) {
  g_wdt_timeout_ms = timeout_s * 1000UL;
  printf("[%lu] wdt timeout %lums\n", g_now, g_wdt_timeout_ms);
  return ESP_OK;
}
#endif
static inline esp_err_t esp_task_wdt_add(TaskHandle_t) { g_wdt_on = true; g_wdt_last = g_now; return ESP_OK; }
static inline esp_err_t esp_task_wdt_reset(void) { g_wdt_last = g_now; return ESP_OK; }
