/* Host-side stub of the ESP-IDF RMT driver (only what examples/argb.iot uses). */
#pragma once
#include <stdint.h>
#include <stddef.h>
typedef int rmt_channel_t;
typedef int esp_err_t;
typedef void *RingbufHandle_t;
#define RMT_CHANNEL_0 0
typedef struct { uint32_t duration0 : 15; uint32_t level0 : 1; uint32_t duration1 : 15; uint32_t level1 : 1; } rmt_item32_t;
typedef struct {
  int rmt_mode; int channel; int gpio_num; int clk_div; int mem_block_num;
  struct { int idle_threshold; int filter_ticks_thresh; bool filter_en; } rx_config;
} rmt_config_t;
#define RMT_DEFAULT_CONFIG_RX(gpio, ch) { 1, ch, gpio, 80, 1, { 12000, 100, true } }
static inline esp_err_t rmt_config(const rmt_config_t *) { return 0; }
static inline esp_err_t rmt_driver_install(int, int, int) { return 0; }
static inline esp_err_t rmt_get_ringbuf_handle(int, RingbufHandle_t *h) { *h = 0; return 0; }
static inline esp_err_t rmt_rx_start(int, bool) { return 0; }
static inline void *xRingbufferReceive(RingbufHandle_t, size_t *s, uint32_t) { *s = 0; return 0; }
static inline void vRingbufferReturnItem(RingbufHandle_t, void *) {}
