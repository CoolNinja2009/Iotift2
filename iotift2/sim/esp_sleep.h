/*
 * Host-side stand-in for ESP-IDF esp_sleep.h.
 * Deep sleep ends the simulation (the real chip restarts on wake-up).
 * Light sleep advances the clock by the timer duration and returns.
 * The boot wake-up cause is set with a "0:WAKE:<timer|ext0|gpio>" event.
 */
#pragma once
#include "Arduino.h"
#include "esp_err.h"
#include "soc/soc_caps.h"

typedef enum {
  ESP_SLEEP_WAKEUP_UNDEFINED, ESP_SLEEP_WAKEUP_ALL, ESP_SLEEP_WAKEUP_EXT0, ESP_SLEEP_WAKEUP_EXT1,
  ESP_SLEEP_WAKEUP_TIMER, ESP_SLEEP_WAKEUP_TOUCHPAD, ESP_SLEEP_WAKEUP_ULP, ESP_SLEEP_WAKEUP_GPIO,
  ESP_SLEEP_WAKEUP_UART,
} esp_sleep_source_t;
typedef esp_sleep_source_t esp_sleep_wakeup_cause_t;
typedef enum { ESP_GPIO_WAKEUP_GPIO_LOW = 0, ESP_GPIO_WAKEUP_GPIO_HIGH = 1 } esp_deepsleep_gpio_wake_up_mode_t;

extern uint64_t g_sleep_timer_us;
extern int g_sleep_pin, g_sleep_level;
extern int g_wake_cause;

static inline esp_err_t esp_sleep_enable_timer_wakeup(uint64_t us) { g_sleep_timer_us = us; return ESP_OK; }
static inline esp_err_t esp_sleep_disable_wakeup_source(esp_sleep_source_t s) {
  if (s == ESP_SLEEP_WAKEUP_TIMER) {
    if (!g_sleep_timer_us) return ESP_ERR_INVALID_STATE;
    g_sleep_timer_us = 0;
  }
  return ESP_OK;
}
#if SOC_PM_SUPPORT_EXT0_WAKEUP || SOC_PM_SUPPORT_EXT_WAKEUP
static inline esp_err_t esp_sleep_enable_ext0_wakeup(gpio_num_t p, int level) {
  g_sleep_pin = (int)p; g_sleep_level = level; return ESP_OK;
}
#endif
#if SOC_GPIO_SUPPORT_DEEPSLEEP_WAKEUP
static inline esp_err_t esp_deep_sleep_enable_gpio_wakeup(uint64_t mask, esp_deepsleep_gpio_wake_up_mode_t m) {
  for (int i = 0; i < 64; i++) if (mask >> i & 1) { g_sleep_pin = i; g_sleep_level = (int)m; }
  return ESP_OK;
}
#endif
static inline esp_err_t esp_sleep_enable_gpio_wakeup(void) { return ESP_OK; }
static inline void _sim_sleep_banner(const char *kind) {
  fflush(stdout);
  printf("[%lu] %s timer=%llums pin=%d level=%d\n", g_now, kind,
         (unsigned long long)(g_sleep_timer_us / 1000), g_sleep_pin, g_sleep_level);
}
__attribute__((noreturn)) static inline void esp_deep_sleep_start(void) {
  _sim_sleep_banner("DEEP SLEEP");
  fflush(stdout);
  exit(0);
}
static inline esp_err_t esp_light_sleep_start(void) {
  _sim_sleep_banner("LIGHT SLEEP");
  g_now += (unsigned long)(g_sleep_timer_us / 1000);
  g_wake_cause = g_sleep_timer_us ? ESP_SLEEP_WAKEUP_TIMER : ESP_SLEEP_WAKEUP_EXT0;
  printf("[%lu] WAKE\n", g_now);
  return ESP_OK;
}
static inline esp_sleep_wakeup_cause_t esp_sleep_get_wakeup_cause(void) {
  return (esp_sleep_wakeup_cause_t)g_wake_cause;
}
