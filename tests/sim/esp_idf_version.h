/* Arduino-ESP32 core 2.x is built on ESP-IDF 4.4, core 3.x on ESP-IDF 5.1. */
#pragma once
#include "Arduino.h"
#if ESP_ARDUINO_VERSION_MAJOR >= 3
#define ESP_IDF_VERSION_MAJOR 5
#else
#define ESP_IDF_VERSION_MAJOR 4
#endif
