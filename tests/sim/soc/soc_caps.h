/*
 * SoC capability macros as ESP-IDF defines them.  Default: original ESP32
 * (ext0 wake-up; the macro was renamed between IDF 4.4 and 5.x).
 * -DIOTIFT_SIM_C3 models an ESP32-C3 (no ext0, GPIO deep-sleep wake-up).
 */
#pragma once
#include "esp_idf_version.h"
#ifdef IOTIFT_SIM_C3
#define SOC_LEDC_CHANNEL_NUM 6
#else
#define SOC_LEDC_CHANNEL_NUM 8
#define SOC_LEDC_SUPPORT_HS_MODE 1
#endif

#ifdef IOTIFT_SIM_C3
#define SOC_GPIO_SUPPORT_DEEPSLEEP_WAKEUP 1
#elif ESP_IDF_VERSION_MAJOR >= 5
#define SOC_PM_SUPPORT_EXT0_WAKEUP 1
#define SOC_PM_SUPPORT_EXT1_WAKEUP 1
#else
#define SOC_PM_SUPPORT_EXT_WAKEUP 1
#endif
