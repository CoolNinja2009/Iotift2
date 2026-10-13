"""The small C++ runtime every generated program carries (namespace iot)."""

RUNTIME = r'''
// -- Iotift runtime ------------------------------------------------------
namespace iot {

[[noreturn]] static void halt() {
#if defined(IOTIFT_SIM)
  fflush(stdout);
  exit(3);
#else
  delay(100);
  abort();
#endif
}

[[noreturn]] __attribute__((unused)) static void panic(const char *msg, const char *file, int line) {
  Serial.printf("\nPANIC: %s (%s:%d)\n", msg, file, line);
  halt();
}

template <class T, size_t N> struct Arr {
  T v[N];
  static constexpr size_t len() { return N; }
};

template <class T, size_t N>
static inline T &at(Arr<T, N> &a, uint64_t i, const char *file, int line) {
  if (i >= N) {
    Serial.printf("\nPANIC: index %llu is out of range for an array of %u (%s:%d)\n",
                  (unsigned long long)i, (unsigned)N, file, line);
    halt();
  }
  return a.v[i];
}
template <class T, size_t N>
static inline const T &at(const Arr<T, N> &a, uint64_t i, const char *file, int line) {
  return at(const_cast<Arr<T, N> &>(a), i, file, line);
}
template <class T, size_t N>
static inline T &at(Arr<T, N> &a, int64_t i, const char *file, int line) {
  return at(a, (uint64_t)(i < 0 ? UINT64_MAX : (uint64_t)i), file, line);
}
template <class T, size_t N>
static inline const T &at(const Arr<T, N> &a, int64_t i, const char *file, int line) {
  return at(const_cast<Arr<T, N> &>(a), i, file, line);
}

static inline bool due(uint32_t now, uint32_t at) { return (int32_t)(now - at) >= 0; }
static inline uint32_t ms(int64_t us) { return us <= 0 ? 0 : (uint32_t)(us / 1000); }

// Held while one print/println writes its pieces, so lines from the two cores never interleave.
struct PrintLock {
#if defined(IOTIFT_SIM)
  PrintLock() {}
#else
  static SemaphoreHandle_t handle() {
    static SemaphoreHandle_t m = xSemaphoreCreateRecursiveMutex();
    return m;
  }
  PrintLock() { xSemaphoreTakeRecursive(handle(), portMAX_DELAY); }
  ~PrintLock() { xSemaphoreGiveRecursive(handle()); }
#endif
  PrintLock(const PrintLock &) = delete;
  PrintLock &operator=(const PrintLock &) = delete;
};

static inline void put_s(const char *s) { Serial.print(s); }
static inline void put_i(int64_t v) {
  char b[24];
  snprintf(b, sizeof b, "%lld", (long long)v);
  Serial.print(b);
}
static inline void put_u(uint64_t v) {
  char b[24];
  snprintf(b, sizeof b, "%llu", (unsigned long long)v);
  Serial.print(b);
}
static inline void put_f(double v, int decimals) {
  char b[48];
  snprintf(b, sizeof b, "%.*f", decimals, v);
  Serial.print(b);
}
static inline void put_b(bool v) { Serial.print(v ? "true" : "false"); }
static inline void put_hex(uint64_t v, bool upper) {
  char b[24];
  snprintf(b, sizeof b, upper ? "%llX" : "%llx", (unsigned long long)v);
  Serial.print(b);
}
static inline void put_bin(uint64_t v) {
  char b[66];
  int n = 0;
  do { b[n++] = (char)('0' + (v & 1)); v >>= 1; } while (v && n < 64);
  char r[66];
  for (int i = 0; i < n; i++) r[i] = b[n - 1 - i];
  r[n] = 0;
  Serial.print(r);
}
static inline void put_dur(int64_t us) {
  if (us % 1000 == 0) { put_i(us / 1000); Serial.print("ms"); }
  else { put_i(us); Serial.print("us"); }
}
static inline void nl() { Serial.println(); }

static inline int64_t uptime() {
#if defined(IOTIFT_SIM)
  return (int64_t)millis() * 1000;
#else
  return esp_timer_get_time();
#endif
}

struct Timer { uint32_t next; uint32_t period; bool running; };
struct Once { uint32_t at; bool done; };
struct Debounce { bool stable; bool cand; uint32_t since; };

// Returns true once per debounced change of `level`.
static inline bool debounce(Debounce &d, bool level, uint32_t now, uint32_t ms) {
  if (level != d.cand) { d.cand = level; d.since = now; return false; }
  if (level != d.stable && now - d.since >= ms) { d.stable = level; return true; }
  return false;
}

}  // namespace iot
'''
