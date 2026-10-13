# Iotift 2 — Language Specification & Design

**Status:** Draft 2 · blocks are `do … end` (decided 2026-10-13) · **Date:** 2026-10-13
**Target:** ESP32 family only (ESP32, S2, S3, C3, C6, H2, P4) · **Output:** a PlatformIO project in C++17 (Arduino-ESP32 core 2.x / 3.x)
**Compiler:** Python 3.8+ (the existing `lexer → parser → semantic → codegen` pipeline)

> This document describes the language as it should be. Section 17 maps today's Iotift (v1, C-like
> syntax) onto this design and lists what the first v2 compiler (`iotift2` 2.0.0a2) already implements.
> Section 18 records the design decisions and the additions still on the table.

---

## Contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [A first look](#2-a-first-look)
3. [Lexical structure](#3-lexical-structure)
4. [Types](#4-types)
5. [Declarations, expressions and control flow](#5-declarations-expressions-and-control-flow)
6. [Errors and optionals](#6-errors-and-optionals)
7. [The non-blocking model: flows, tasks and `wait`](#7-the-non-blocking-model)
8. [Cores and isolation](#8-cores-and-isolation)
9. [Events, timers and interrupts](#9-events-timers-and-interrupts)
10. [Hardware: pins, buses and peripherals](#10-hardware-pins-buses-and-peripherals)
11. [First-class WiFi and networking](#11-first-class-wifi-and-networking)
12. [C and C++ interop](#12-c-and-c-interop)
13. [Modules, packages and large projects](#13-modules-packages-and-large-projects)
14. [Standard library](#14-standard-library)
15. [Safety profiles and compile-time checks](#15-safety-profiles-and-compile-time-checks)
16. [Compilation model and generated code](#16-compilation-model-and-generated-code)
17. [Migration from Iotift v1 and roadmap](#17-migration-from-iotift-v1-and-roadmap)
18. [Open design questions and suggested additions](#18-open-design-questions-and-suggested-additions)
19. [Appendix A: grammar sketch](#appendix-a-grammar-sketch)
20. [Appendix B: keyword list](#appendix-b-keyword-list)

---

## 1. Goals and non-goals

### Goals

| # | Goal | What it means in practice |
|---|------|---------------------------|
| G1 | **Non-blocking by construction** | There is no `delay()`. Waiting is a language keyword (`wait`) that suspends only the current flow; the core keeps running everything else. Blocking C++ calls are tracked by the type system and cannot be called where they would stall a core. |
| G2 | **Trivial core selection** | Put code under `core 0` or `core 1` and it runs there. Data owned by one core cannot be touched by the other except through `chan`, `signal` or `shared` values. The compiler proves there are no data races between cores. |
| G3 | **First-class WiFi and networking** | `wifi`, `http`, `mqtt`, `udp`, `ntp`, `ota` are declarations with events, not libraries you wire up by hand. |
| G4 | **First-class C headers and C++ libraries** | `use c.math` gives typed access to `<cmath>`. Any PlatformIO library can be bound with `extern cpp` (hand-written or generated with `iotift bind`). |
| G5 | **Statically typed, zero-cost** | Every type is known at compile time, generics are monomorphised, there is no garbage collector and no heap use unless you opt in. Generated C++ is what a careful embedded engineer would write. |
| G6 | **Easy for beginners** | A blinking LED is three lines. No headers, no `setup()`/`loop()`, no semicolons, no pointer syntax, friendly error messages that say how to fix the problem. |
| G7 | **Scales to large codebases** | Modules, packages, visibility, interfaces (traits), generics, a package manifest, incremental builds, a formatter, a linter, an LSP and host-side tests. |
| G8 | **Assurance-friendly** | A `safety` profile (no heap, no recursion, no raw C++, every error handled, bounded waits) gives a subset suitable for high-assurance work, with deterministic builds and source-mapped output. |
| G9 | **Distinct syntax** | Keyword-led `do … end` blocks, no braces or semicolons, readable aloud. It should not look like C. |

### Non-goals

- Other microcontroller families. The ESP32 is the only target (this matches the project's current scope).
- A runtime, VM or interpreter on the device. Iotift compiles away.
- Replacing ESP-IDF or Arduino. Iotift sits on top of them and leans on them.
- Making certification claims. The `safety` profile makes a certification argument easier to build; it does not replace the process (DO-178C, IEC 61508, MIL-STD-882E and so on). Note too that the ESP32 silicon and Espressif stacks are not themselves qualified for those standards.

---

## 2. A first look

### Blink (the whole program)

```iotift
pin led = out 2

every 500ms do
    led.toggle()
end
```

### Blink as a flow

```iotift
pin led = out 2

task blink do
    loop do
        led.on()
        wait 200ms
        led.off()
        wait 800ms
    end
end
```

`wait` suspends the `blink` task only. Nothing else on the core is held up.

### A realistic two-core device

```iotift
device esp32s3

use std.wifi
use std.mqtt
use std.log
use c.math

pin heater = pwm 4, freq 1kHz, bits 10
pin probe  = adc 1, atten 11db
pin button = in 0, pull up, debounce 30ms

struct Sample do
    celsius: f32
    at: Instant
end

let samples = chan[Sample, 16]          -- crosses cores safely
shared var setpoint: f32 = 21.5         -- readable/writable from both cores

core 1 as control do                       -- hard real-time work lives here
    var pid = Pid(kp: 2.0, ki: 0.1, kd: 0.5)

    every 10ms do
        let c = to_celsius(probe.read_mv())
        let duty = pid.step(target: setpoint.get(), actual: c)
        heater.duty(math.clamp(duty, 0.0, 1.0))
        _ = samples.try_send(Sample(celsius: c, at: now()))   -- drop the sample if the queue is full
    end

    on button.press do
        setpoint.update(|s| s + 0.5)
    end
end

core 0 as network do                       -- WiFi, MQTT, logging
    wifi home do
        ssid: "Workshop"
        password: secret("WIFI_PASS")
        retry: backoff(1s, max: 1min)
    end

    mqtt broker on home do
        url: "mqtts://broker.local:8883"
        client_id: "oven-{chip.mac_suffix}"
    end

    on home.connected do
        log.info("online at {home.ip}")
    end

    task publisher do
        loop do
            let s = wait samples.recv()
            wait broker.publish("oven/temp", "{s.celsius:.1}") else |e| do
                log.warn("publish failed: {e}")
            end
        end
    end

    on broker.message("oven/setpoint") |msg| do
        setpoint.set(msg.parse_f32() or setpoint.get())
    end
end

fn to_celsius(mv: u32) -> f32 => (mv as f32 - 500.0) / 10.0
```

What the compiler does for you here: pins and PWM channels allocated and checked against the S3 pin map,
two executors pinned to cores 0 and 1, a cross-core queue for `samples`, an atomic for `setpoint`,
the WiFi state machine with backoff, an MQTT client with TLS and reconnection, and a proof that `pid`
is only ever touched from core 1.

---

## 3. Lexical structure

### 3.1 Source files

- UTF-8, extension `.iot`. One file is one module.
- **Every block is `do … end`.** The rule is the same everywhere: `fn`, `if`, `while`, `for`, `loop`,
  `match`, `struct`, `enum`, `impl`, `task`, `every`, `after`, `on` and `core` all open their block with
  `do` and close it with `end`. Indentation is free-form (the formatter normalises it to 4 spaces), so
  code pasted from a chat app or a forum still compiles.
- `if` chains continue with `elif … do` and `else`, and the whole chain has one `end`:

  ```iotift
  if t > 30.0 do
      fan.on()
  elif t < 10.0 do
      heater.on()
  else
      idle()
  end
  ```

  `else if` is rejected with a hint to write `elif`, so there is never a question of which `end` closes what.
- **One-statement blocks** use `=>` and need no `end`: `if hot => fan.on()`, `every 1s => led.toggle()`,
  `fn sq(x: f32) -> f32 => x * x`. A short block can also sit on one line: `every 1s do led.toggle() end`.
- **No semicolons.** A newline ends a statement. A line continues automatically inside `( )`, `[ ]`,
  after a binary operator or a comma, or after a trailing `\`. (`;` appears only inside array types: `[u8; 16]`.)
- Braces are not used for blocks at all; `{` in code is an error that points to `do … end`.

### 3.2 Comments

```iotift
-- line comment
--- doc comment, attached to the next declaration (shown by the LSP and `iotift doc`)
{- block comment, {- nestable -} -}
```

### 3.3 Identifiers and naming

- `snake_case` for values, functions, modules; `PascalCase` for types, traits and enum variants are lowercase
  (`Mode.heat`). The formatter and linter enforce this, which keeps big codebases uniform.
- Identifiers that collide with Arduino/ESP-IDF macros (`HIGH`, `PI`, `DEC`, `B1`…) are allowed in Iotift
  source; the code generator gives every user name a trailing underscore (`count` → `count_`) and prefixes
  everything it invents with `iot_`, so macro and keyword collisions are impossible by construction (the
  v1 compiler rejects them instead).

### 3.4 Literals

| Kind | Examples | Notes |
|------|----------|-------|
| Integer | `42`, `1_000_000`, `0xFF`, `0b1010_0001`, `0o17` | Type inferred, defaults to `i32`; suffixes `42u8`, `7i64` |
| Float | `3.14`, `1e-3`, `2.5f64` | Defaults to `f32` (the ESP32 FPU is single-precision) |
| Bool | `true`, `false` | |
| Char / byte | `'a'`, `b'\n'` | `char` is a Unicode scalar, `byte` is `u8` |
| String | `"temp {t:.1} C"` | Interpolation with `{expr[:fmt]}`, `{{` / `}}` escape braces |
| Raw string | `r"C:\path"`, `r#"has "quotes""#` | No escapes, no interpolation |
| Bytes | `b"\x01\x02AB"` | `[u8; N]` |
| Duration | `10us`, `5ms`, `2s`, `1.5s`, `1min`, `1h` | Type `Duration` (µs resolution, signed 64-bit) |
| Frequency | `50Hz`, `20kHz`, `40MHz` | Type `Frequency` |
| Size | `4kb`, `1mb` | Bytes, used for stack sizes and buffers |
| Baud | `115200baud` | Type `Baud` |
| IPv4 | `ip"192.168.1.10"` | Checked at compile time |
| MAC | `mac"24:6F:28:AA:BB:CC"` | Checked at compile time |

Units are real types: `wait 500` is a compile error ("did you mean `500ms`?"), and `Duration * 2` is fine
but `Duration + Frequency` is not. This removes a whole class of "was that ms or µs?" bugs.

### 3.5 Operators

| Precedence (high → low) | Operators |
|---|---|
| Postfix | `x.f`, `f(x)`, `a[i]`, `x?` (optional chaining) |
| Unary | `-x`, `~x` (bitwise not) |
| Cast | `x as f32` |
| Multiplicative | `*`, `/`, `%` |
| Additive | `+`, `-`, `+%`, `-%`, `*%` (explicit wrapping), `+|` `-|` (saturating) |
| Shift | `<<`, `>>` |
| Bitwise | `&`, then `^`, then `\|` |
| Range | `..` (exclusive), `..=` (inclusive) |
| Comparison | `==`, `!=`, `<`, `<=`, `>`, `>=`, `in` |
| Logical | `not`, then `and`, then `or` |
| Fallback | `x or default`, `x else \|e\| …` (see §6) |
| Assignment | `=`, `+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `\|=`, `^=`, `<<=`, `>>=` |

Words (`and`, `or`, `not`) replace `&&`, `||`, `!`, which reads better for beginners. `not` binds looser
than comparison, so `not a == b` means `not (a == b)`. There is no `++`/`--`. Comparisons do not chain
(`a < b < c` is an error that suggests `a < b and b < c`).
Assignment is a statement, not an expression, so `if x = 5` cannot happen.

Bitwise operators bind tighter than comparison (unlike C), so `if flags & MASK == 0` means what it looks like.

---

## 4. Types

### 4.1 Primitive types

| Type | Size | C++ | Notes |
|------|------|-----|-------|
| `i8 i16 i32 i64` | 1–8 B | `int8_t` … `int64_t` | |
| `u8 u16 u32 u64` | 1–8 B | `uint8_t` … `uint64_t` | |
| `usize` | 4 B | `size_t` | Indexes and lengths |
| `f32` / `f64` | 4 / 8 B | `float` / `double` | `f64` is software-emulated on ESP32; the linter flags it in hot paths |
| `bool` | 1 B | `bool` | |
| `byte` | 1 B | `uint8_t` | Alias of `u8` for readability |
| `char` | 4 B | `char32_t` | |
| `Duration`, `Instant`, `Frequency`, `Baud` | 8 / 8 / 4 / 4 B | runtime structs | Unit-safe |
| `Ip4`, `Mac` | 4 / 6 B | runtime structs | |

Integer overflow: **checked** in `debug` builds (panic with source line), **wrapping** in `release`
unless the `safety` profile is on, where overflow checks stay enabled. Explicit wrapping/saturating
operators exist for code that wants either behaviour deliberately.

Implicit conversions: only lossless widening (`u8 → u32`, `i16 → i32`, `f32 → f64`, any int → `f32` when exact).
Everything else needs `as`. `as` between numeric types truncates/rounds like C++ but is range-checked in
debug builds.

### 4.2 Strings and text (no hidden heap)

| Type | Meaning |
|------|---------|
| `str` | Immutable UTF-8 view: string literals, slices of buffers. Never owns memory. |
| `Text[N]` | Fixed-capacity, owned, mutable string with room for `N` bytes. Lives on the stack or in a frame. `append`, `clear`, `format`, `len`, `cap`. Overflow returns an error (or truncates with `append_trunc`). |
| `String` | Heap string, only available with `use std.heap` (and forbidden in `safety`). |

Interpolated literals produce a value of type `Fmt` that is written directly into whatever consumes it
(a `Text`, the log, a socket), so `log.info("t={t}")` never allocates.

### 4.3 Arrays, slices, buffers

```iotift
var raw: [u16; 64]                    -- fixed array, zero-initialised
let table = [1, 2, 4, 8]              -- [i32; 4]
fn avg(xs: [f32]) -> f32              -- [T] is a slice (pointer + length), borrowed
var queue: List[Sample, 32]           -- fixed-capacity vector: push / pop / len / is_full
var ring: Ring[u8, 256]               -- ring buffer, ideal for UART/DMA
var map: Map[str, u16, 16]            -- fixed-capacity open-addressing map
```

Indexing is bounds-checked (panic in debug, and in `safety`; compiled out in `release` only where the
compiler can prove the index is in range, or with `@unchecked`).

### 4.4 Structs, enums and methods

```iotift
struct Reading do
    id: u16
    celsius: f32 = 0.0                -- field default
    ok: bool = true
end

enum Mode do
    off
    heat
    cool
    hold(until: Instant)              -- variants can carry data (tagged union)
end

impl Reading do
    fn fahrenheit(self) -> f32 => self.celsius * 1.8 + 32.0
    fn reset(mut self) do                -- `mut self` may modify
        self.celsius = 0.0
    end
end

let r = Reading(id: 1, celsius: 21.5)       -- named arguments, in any order
let mode = Mode.hold(until: now() + 10min)
```

Structs are value types, copied on assignment when small (≤ 16 bytes) and passed by reference otherwise
(the compiler chooses; semantics are always value semantics). `@packed` and `@align(n)` exist for wire
formats and DMA buffers.

`match` must be exhaustive:

```iotift
match mode do
    Mode.off => heater.off()
    Mode.heat | Mode.cool => regulate()
    Mode.hold(until) if now() < until => pass
    Mode.hold(_) => mode = Mode.off
end
```

### 4.5 Traits and generics

```iotift
trait Sensor do
    fn read(mut self) -> f32 ! SensorError     -- `! E` = may fail, see §6
    fn name(self) -> str => "sensor"           -- default method
end

struct Ntc do
    pin: AdcPin
    beta: f32
end

impl Sensor for Ntc do
    fn read(mut self) -> f32 ! SensorError do
        let mv = self.pin.read_mv()
        if mv == 0 => fail SensorError.open_circuit
        return ntc_to_celsius(mv, self.beta)
    end
end

fn log_all[S: Sensor](sensors: [S]) do
    for s in sensors do
        log.info("{s.name()}: {s.read() or f32.nan}")
    end
end
```

- Generics are monomorphised (one C++ instantiation per concrete type; no vtables by default).
- `dyn Sensor` gives dynamic dispatch (a C++ abstract class) when you need a heterogeneous list.
- Const generics: `Text[N]`, `List[T, N]`, `fn checksum[N: usize](b: [u8; N]) -> u16`.

### 4.6 References and ownership (kept deliberately small)

Iotift has no pointers in the surface language. It has:

- **Values** (the default).
- **Borrowed parameters**: function parameters are read-only borrows; `mut` parameters are exclusive
  borrows (`fn fill(mut buf: [u8])`). A borrow cannot be stored in a global or in a flow frame across a `wait`.
- **Handles**: hardware objects (`pin`, `bus`, `wifi` …) are unique handles declared once and referred to by name.

That is enough to rule out dangling pointers and aliasing bugs without asking beginners to learn lifetimes.
Raw pointers exist only inside `extern cpp` bindings and `cpp` blocks (§12).

### 4.7 Type inference

Local `let`/`var` infer their type from the initialiser. Function signatures, struct fields and globals
are always written out (that keeps large codebases readable and makes separate compilation possible).

---

## 5. Declarations, expressions and control flow

### 5.1 Bindings

```iotift
let limit = 80.0          -- immutable
var count: u32 = 0        -- mutable
const BAUD = 115200baud   -- compile-time constant, usable in types and pin numbers
static var boots: u32 = 0 @rtc        -- survives deep sleep (RTC memory)
static var calib: Calib @nvs("cal")   -- persisted to NVS, loaded at boot (see §14)
```

Globals at module level are `let`, `var` or `const`. Every `var` global has an **owner core** (§8).

### 5.2 Functions

```iotift
fn clamp(x: f32, lo: f32, hi: f32) -> f32 do
    if x < lo => return lo
    if x > hi => return hi
    return x
end

fn area(w: f32, h: f32) -> f32 => w * h            -- expression body

fn connect(host: str, port: u16 = 1883, tls: bool = false)   -- defaults

connect("broker.local", tls: true)                 -- named args after positional
```

- The last expression of an `=>` body is the result. Block bodies use `return`.
- Plain `fn` **cannot suspend**: it must not contain `wait` or call anything that suspends. It runs to completion.
  This is what keeps handlers predictable (§7).
- Recursion is allowed in plain `fn` (with a lint), forbidden in `flow` and under the `safety` profile.

### 5.3 Control flow

```iotift
if t > 30.0 do
    fan.on()
elif t < 10.0 do
    heater.on()
else
    idle()
end

let label = if ok then "ok" else "fault"    -- `if` as an expression

while queue.len() > 0 do
    process(queue.pop())
end

for i in 0..10            -- 0 to 9
for i in 0..=10 by 2      -- 0, 2, … 10
for (i, s) in samples.enumerate()
for b in buf[4..]

loop do                      -- infinite; inside a flow it must contain a `wait` (compile error otherwise)
    ...
end

break, continue, return, pass
```

Labelled loops: `loop as outer` … `break outer`.

### 5.4 `defer`

```iotift
fn write_frame(mut bus: I2c, addr: u8, data: [u8]) ! I2cError do
    try bus.begin(addr)
    defer bus.end()                 -- runs on every exit path, including errors
    try bus.write(data)
end
```

---

## 6. Errors and optionals

There are no exceptions. Failure is part of the type.

| Syntax | Meaning | C++ |
|--------|---------|-----|
| `T?` | Optional value: `some(x)` or `none` | `iot::Opt<T>` |
| `T ! E` | Result: a `T` or an error of type `E` | `iot::Res<T,E>` |
| `T !` | Result with the module's or the standard `Error` | |

```iotift
enum SensorError do
    timeout
    crc
    open_circuit
end

fn read() -> f32 ! SensorError do
    let raw = try bus.read16(0x40)            -- `try` propagates the error to the caller
    if not crc_ok(raw) => fail SensorError.crc
    return convert(raw)

-- Handling, from simplest to most explicit:
end
let a = read() or 0.0                         -- fallback value
let b = read() else |e| do                       -- handle and recover
    log.warn("read failed: {e}")
    0.0
end
match read() do
    ok(v) => show(v)
    err(SensorError.timeout) => retry_later()
    err(e) => panic("sensor: {e}")
end
```

- Ignoring a result is a compile error (`_ = read()` makes discarding explicit).
- `panic(msg)` calls the configured panic policy: `reboot` (default), `halt`, or a user `on panic` handler,
  after logging the message, source location and core. In `safety`, every `panic` site is listed in the build report.
- Optionals use the same operators: `name? .len()`, `x or default`, `if let v = maybe`.

---

## 7. The non-blocking model

This is the heart of the language.

### 7.1 Three kinds of code

| Kind | Declared with | May `wait`? | Runs as |
|------|---------------|-------------|---------|
| **Function** | `fn` | No | A normal C++ function. Runs to completion. |
| **Flow** | `flow` | Yes | A resumable function (a stackless coroutine compiled to a state machine). |
| **Task** | `task` | Yes | A flow started automatically at boot (or by `start`). Lives forever or until it returns. |

Event handlers (`every`, `after`, `on …`) are bodies of plain code: they may call `fn`s, send on channels,
and **start** flows, but they may not `wait` themselves. That rule keeps every handler short and bounded.
If a handler needs to wait, write it as `on button.press => start beep()` or put the logic in a task.

### 7.2 `wait`

`wait` is the only way time passes. It suspends the current flow and gives the core back to the executor.

```iotift
wait 250ms                                  -- sleep this flow only
wait until door.is_open() timeout 5s        -- poll a condition (re-checked every executor tick)
let msg = wait inbox.recv()                 -- wait for a channel
let ok = wait wifi.connected timeout 30s    -- wait for an event; returns false on timeout
let r = wait http.get(url)                  -- wait on another flow
wait any                                    -- see `select` below
```

Calling a flow from a flow requires `wait`; calling a flow without `wait` is a compile error unless you
`start` it (run it concurrently and get a `Handle[T]`):

```iotift
flow fetch_weather() -> Weather ! NetError do
    let resp = try wait http.get("https://api.example/w")
    return try json.decode[Weather](resp.body)
end

task updater do
    loop do
        let w = wait fetch_weather() else |e| do
            log.warn("weather: {e}")
            wait 1min
            continue
        end
        display.show(w)
        wait 15min
    end
end
```

### 7.3 `select`

```iotift
select do
    cmd = wait commands.recv() => run(cmd)
    wait button.press           => menu.next()
    wait 10s                    => screen.dim()
end
```

The first branch to become ready wins; the others are cancelled cleanly (channel receives are not consumed).

### 7.4 Concurrency inside a flow

```iotift
let (a, b) = wait all(read_imu(), read_baro())         -- run both, wait for both
let first = wait race(read_primary(), read_backup())   -- first to finish wins, other cancelled
let h = start log_upload()                             -- fire and keep the handle
...
h.cancel()
```

Cancellation is cooperative: a cancelled flow stops at its next `wait`, runs its `defer`s, and its
handle resolves to `err(Cancelled)`.

### 7.5 Blocking C++ calls

Many Arduino libraries block (`delay()` inside a sensor driver, `WiFiClient::connect`, SD card writes).
Iotift does not pretend otherwise:

- In an `extern cpp` binding, a function marked `blocking` (or `blocking(max: 50ms)`) **cannot be called
  from a handler or a flow** directly. The compiler says:

  ```
  error[E0412]: `Dht.read_temperature` blocks for up to 250ms and would stall core 1
    --> src/climate.iot:14:17
     |
  14 |     let t = dht.read_temperature()
     |             ^^^^^^^^^^^^^^^^^^^^^^ blocking call
     = help: run it on a worker so this flow can keep going:
             let t = wait offload dht.read_temperature()
  ```

- `wait offload expr` runs the call on a **worker thread** (a real FreeRTOS task from a small pool, by
  default on the same core at lower priority) and resumes the flow when it returns.
- `thread` tasks (`task cam thread(stack: 8kb)`) are real FreeRTOS tasks where blocking calls are allowed.
  Use them for big libraries that were written around `delay()`.
- Unannotated extern functions are assumed **non-blocking** but can be marked `@may_block` by a
  linter rule or the `bind` tool when it sees `delay`, `vTaskDelay`, `xSemaphoreTake(..., portMAX_DELAY)`
  in the source.

### 7.6 Runtime model

- Each core used by the program runs one **executor**: a FreeRTOS task pinned with
  `xTaskCreatePinnedToCore`. It keeps a ready queue, a timer heap of pending wake-ups and an event queue
  fed by ISRs and other cores.
- When nothing is ready, the executor blocks on `ulTaskNotifyTake` with a timeout equal to the next
  deadline. The core is genuinely idle (FreeRTOS idle task runs, light sleep and tickless idle work),
  not spinning in `loop()`.
- Flows are **stackless**: their locals that live across a `wait` are hoisted into a frame struct whose
  size is known at compile time. Frames are statically allocated (one per task, a fixed pool per `start`
  site, size set by `@pool(n)`), so there is no heap allocation and no per-flow stack. Thousands of
  concurrent flows cost a few bytes each.
- Scheduling inside one executor is cooperative and deterministic: ready flows run in priority order,
  then FIFO. Handlers run to completion.
- **Starvation guard:** a handler or flow segment that runs longer than its budget (default 10 ms,
  configurable per core with `budget`) is reported in debug builds with the source line, before the
  task watchdog would fire. Long CPU loops can insert `wait yield`.

### 7.7 Why not just FreeRTOS tasks?

Real tasks each need their own stack (2–8 KB each), need locks around every shared variable and make
races easy. Iotift gives you cheap stackless flows by default, real threads only where you ask, and a
compiler that checks the boundary.

---

## 8. Cores and isolation

### 8.1 Placing code on a core

```iotift
core 1 as control do
    budget 2ms
    priority 20            -- FreeRTOS priority of this core's executor (default 5)

    every 1ms do              -- runs on core 1
        ...
    end
end

core 0 as network do
    wifi home do
        ...
    end
end
```

- Anything outside a `core` block goes on the **default core** (`core 1` on dual-core chips, which is
  where Arduino's `loop()` runs; WiFi/BT stacks stay on core 0 as Espressif configures them).
- A `task` can also be placed individually: `task logger on core 0`.
- `core any` lets the scheduler pick (an unpinned FreeRTOS task) for work that does not care.
- On single-core chips (ESP32-S2, C3, C6, H2), `core 0` and `core 1` are both mapped onto the one core
  and the compiler prints a note once. With `strict_cores = true` in the manifest it is an error instead.
  Code stays portable across chips; isolation rules still apply, so code written for one core layout is
  race-free on the other.

### 8.2 Ownership of data

Every mutable global belongs to exactly one core: the core of the block it is declared in, or the
default core. **Only code running on that core may read or write it.** The compiler enforces this by
tracking which core every handler, task and function call runs on.

```iotift
core 1 as control do
    var integral: f32 = 0.0
end

core 0 as network do
    on mqtt.message("reset") do
        integral = 0.0        -- error[E0701]: `integral` is owned by core 1 (control)
                              --   help: send a message, or declare it `shared var`
    end
end
```

Functions that touch no globals are core-neutral and can be called anywhere. Functions that touch core-1
globals can only be called from core 1. This is inferred; no annotations needed.

### 8.3 Crossing cores safely

| Primitive | Declaration | Use | Implemented as |
|-----------|-------------|-----|----------------|
| Channel | `let q = chan[Sample, 16]` | Many-to-one or many-to-many message passing. `send` (waits when full), `try_send`, `recv` (waits), `try_recv` | Same core: ring buffer. Cross-core: FreeRTOS queue + executor notification |
| Signal | `let ready = signal` | Wake waiters without data. `raise`, `wait ready` | Event group bit |
| Shared value | `shared var x: T = …` | `get`, `set`, `update(\|v\| …)`, `lock` | `std::atomic` for ≤ 4-byte types; a spinlock (`portMUX`) for larger ones |
| Watch | `let cfg = watch[Config]` | Single writer, many readers always see the latest value | Seqlock |

```iotift
shared var stats: Stats

lock stats as s do           -- critical section; may not contain `wait`, may not call blocking code
    s.count += 1
    s.last = now()
end
```

A `lock` block that contains `wait`, calls a flow, or locks two shared values in different orders in
different places is rejected at compile time. Deadlock-by-lock-ordering and holding a lock across a
suspension are both impossible.

### 8.4 Core-local ISR routing

Interrupts are installed from the core that owns their handler (`gpio_install_isr_service` is called on
that core), so a pin event declared under `core 1` is serviced by core 1.

---

## 9. Events, timers and interrupts

### 9.1 Timers

```iotift
every 100ms                      -- fires at 100ms, 200ms, …
every 1s offset 250ms            -- first fire at 250ms
every 1ms precise                -- driven by esp_timer, jitter < 50 µs; body must be short
after 5s do                         -- once
    log.info("boot settled")
end

every 2s as heartbeat do
    led.toggle()
end

heartbeat.stop()
heartbeat.start()
heartbeat.period = 500ms
if heartbeat.running => ...
```

- Ordinary timers are dispatched by the core's executor. Missed periods are **not** replayed (the timer
  re-aligns), unless `catch_up` is given.
- `precise` timers run their body at ISR-like priority from an `esp_timer` callback and get the `isr`
  restrictions (§9.4).

### 9.2 Pin events

```iotift
pin button = in 0, pull up, debounce 30ms

on button.press             -- falling edge with pull up, rising edge with pull down
on button.release
on button.hold(2s)          -- held for 2 seconds
on button.click(2)          -- double-click
on button.change |level|
```

Pin events are **interrupt-driven**: a GPIO ISR timestamps the edge and posts it to the owning core's
executor, where debouncing and the handler run. Nothing is lost between executor ticks, and handlers are
ordinary code (they may log, start flows, send). (v1 polls from `loop()`; see §17.)

### 9.3 Value events

```iotift
on probe.read_mv() > 2500 every 50ms hysteresis 100     -- sampled condition, fires once per crossing
on battery.percent() < 15 once
```

### 9.4 Interrupt handlers

```iotift
isr on encoder_a.change do
    ticks += if encoder_b.level() then 1 else -1     -- `ticks` must be `shared` or `isr var`
end
```

ISR bodies are checked: no `wait`, no `log`, no allocation, no blocking calls, no non-`IRAM` calls, no
`f64`, no loops without a constant bound. They compile to `IRAM_ATTR` functions. The safe way to do more
work is `raise` a signal or `try_send` to a channel and let a flow pick it up.

### 9.5 System events

```iotift
on boot                     -- before tasks start
on ready                    -- after every peripheral and task has started
on panic |info|             -- last-chance handler (no wait)
on wake(from: Wake.timer)   -- after deep sleep
on low_memory(below: 20kb)
```

---

## 10. Hardware: pins, buses and peripherals

### 10.1 Device

```iotift
device esp32s3              -- optional if set in iotift.toml
```

The device selects the **pin map**: valid GPIO numbers, input-only pins, strapping pins, flash/PSRAM pins,
ADC units, touch pins, LEDC channel and timer counts, number of cores. Every check in §15 uses it.

### 10.2 Pins

```iotift
pin led    = out 2
pin relay  = out 26, active low, initial off
pin button = in 0, pull up, debounce 30ms
pin probe  = adc 34, atten 11db, samples 8          -- averaged read
pin fan    = pwm 13, freq 25kHz, bits 10
pin touch  = touch 4, threshold 40
pin dac1   = dac 25
```

| Method | On | Notes |
|--------|----|-------|
| `on() off() toggle() set(b) is_on()` | `out` | `active low` is handled for you |
| `level() is_high() is_low()` | `in` | |
| `read() read_mv() read_ratio()` | `adc` | `read_mv` uses the eFuse calibration |
| `duty(ratio) write(raw) freq(f)` | `pwm` | `ratio` is `0.0..=1.0` |
| `fade(to: ratio, over: Duration)` | `pwm` | Hardware LEDC fade, no CPU |

Pins can be passed to functions as typed handles: `fn blink(p: OutPin, times: u8)`.

### 10.3 Buses and attached devices

```iotift
bus sensors = i2c sda 21, scl 22, speed 400kHz
bus flash   = spi sck 18, miso 19, mosi 23, speed 20MHz
bus gps     = uart 2, rx 16, tx 17, baud 9600baud
bus car     = can rx 4, tx 5, speed 500kbps                 -- TWAI

attach imu  = Mpu6050 on sensors at 0x68                    -- a driver type from a package
attach card = SdCard on flash, cs 5
```

Bus methods are flows where the hardware is slow (`wait sensors.read(addr, reg, 6)`), and use DMA or the
driver's interrupt where available, so a 20 ms I²C transfer does not block anything.

### 10.4 Other peripherals (first-class declarations)

`rmt` (WS2812/NeoPixel strips: `strip lights = ws2812 on 48, count 60`), `pcnt` (pulse counter / encoders),
`mcpwm` (motor control), `i2s` (audio), `touch`, `ulp` (later). Each follows the same pattern: a declaration
with configuration, properties for state, methods (flows where slow) for actions, events for changes.

---

## 11. First-class WiFi and networking

The v1 WiFi design (milestone 8: declare-don't-call, properties for state, events over polling) carries
over, with the new syntax.

### 11.1 WiFi

```iotift
wifi home do
    ssid: "Workshop"
    password: secret("WIFI_PASS")      -- read from .env / environment at build time, never in source
    hostname: "oven-1"
    retry: backoff(1s, max: 1min)      -- none | fixed(5s) | forever | backoff(...)
    power_save: modem
end

wifi setup_ap do
    mode: ap
    ssid: "Oven-Setup"
    password: "changeme123"
    channel: 6
    max_clients: 2
end

on home.connected do ... end            -- events
on home.lost do ... end
on home.failed do ... end               -- only with `retry: none`
on setup_ap.client_joined do ... end
on setup_ap.client_left do ... end

home.state      -- WifiState.idle | connecting | connected | retrying | failed
home.connected  -- bool
home.ip         -- str, "" when offline
home.rssi       -- i32, 0 when offline
home.mac        -- str
home.connect()  -- start again after `disconnect()`, `autoconnect: false` or `failed`
home.disconnect()
setup_ap.clients, setup_ap.ip, setup_ap.mac

task report do
    wait until home.connected timeout 20s
    ...
end
```

What the compiler does with this (implemented in `iotift2`):

- **Settings.** Station: `ssid`, `password`, `hostname`, `retry`, `timeout` (per attempt, default 15s),
  `power_save` (`none` / `modem` / `max`), `autoconnect` (default `true`). Access point: `mode: ap`,
  `ssid`, `password`, `channel` (1 to 13), `max_clients` (1 to 10). SSIDs are checked to be 1 to 32 bytes,
  WPA2 passwords 8 to 63 characters, hostnames a valid DNS label. One station and one access point at
  most; both together use `WIFI_AP_STA`.
- **Secrets.** `secret("NAME")` is read at build time from the environment, then from a `.env` file next
  to the main file. A missing secret is a compile error. The value ends up in the firmware (it has to),
  never in the source.
- **Retry.** `forever` (the default) is `backoff(1s, max: 1min)`. A lost link is retried at once, then the
  policy applies. Arduino's own auto-reconnect is turned off so there is one policy, and
  `WiFi.persistent(false)` keeps credentials out of flash.
- **Non-blocking.** The driver's event callback only counts events in atomics. The executor of the core
  the `wifi` is declared on runs the connect/retry state machine and the handlers, so handlers never run
  on the system event task. `wifi` declarations and their events follow the ownership rules of §8: using
  `home` from the other core is a compile error.
- **Hardware checks.** No WiFi on the ESP32-H2; on the original ESP32, ADC2 pins (0, 2, 4, 12–15, 25–27)
  cannot be `adc` pins in a program with WiFi.
- Planned: `wait home.connected timeout 20s` as an expression returning `bool`, scanning, static IP,
  `nvs(...)` credentials and provisioning.

Credentials can also come from NVS (`password: nvs("wifi.pass")`) or a provisioning flow
(`provision home via ap(setup_ap)` serves a small captive portal page and stores the result in NVS).

### 11.2 Networking built on WiFi

```iotift
http api on home do                        -- HTTPS client with a connection pool
    base: "https://api.example.com"
    timeout: 10s
    ca: file("certs/root.pem")
end

let r = wait api.get("/v1/config")      -- flow; returns Response ! HttpError
let cfg = try json.decode[Config](r.body)

server web on home, port 80 do             -- HTTP server; routes are handlers
    get "/" => page("index.html")
    get "/temp" => json(Temp(c: last_temp))
    post "/setpoint" |req| do
        setpoint.set(try req.json[f32]())
    end
end

mqtt broker on home do
    url: "mqtts://broker.local:8883"
    will: ("oven/status", "offline", retain: true)
end

on broker.message("oven/+/cmd") |topic, payload| ...
wait broker.publish("oven/temp", "21.5", qos: 1)

ntp clock on home => servers: ["pool.ntp.org"], tz: "CET-1CEST,M3.5.0,M10.5.0/3"
ota updates on home => url: "https://fw.example.com/oven.bin", sign: key("ota.pub")
udp beacon on home, port 4210
ws socket on web, path "/ws"
mdns on home => name: "oven-1", services: [("_http._tcp", 80)]
```

All network operations are flows. None of them block a core.

`ble` and `espnow` follow the same declaration pattern (phase 3).

---

## 12. C and C++ interop

### 12.1 C standard headers, first class

```iotift
use c.math              -- <cmath>: sin, cos, atan2, sqrt, fmod, hypot, … fully typed
use c.string            -- memcpy/memcmp on slices (bounds-checked wrappers)
use c.stdlib            -- abs, div, strtol…  (no malloc/free outside `std.heap`)
use c.time              -- time_t, strftime, …
use c.limits            -- INT32_MAX etc. as constants

let angle = math.atan2(y, x)
let d = math.hypot(dx, dy)
```

The compiler ships typed binding files for these headers (in `iotift/c/`), so calls are type-checked,
overloads on `f32`/`f64` pick the right C function (`sinf` vs `sin`), and the generated code is a direct
call with no wrapper. Common functions (`abs`, `min`, `max`, `clamp`, `sqrt`, `sin`, `cos`, `pow`) are also
in the prelude for beginners.

### 12.2 Binding any C++ library

```iotift
pub extern cpp "Adafruit_NeoPixel.h" from lib "adafruit/Adafruit NeoPixel@^1.12.3" do
    const NEO_GRB: u32
    const NEO_KHZ800: u32

    class Adafruit_NeoPixel as NeoPixel do
        new(count: u16, pin: i16, kind: u32 = NEO_GRB + NEO_KHZ800)
        fn begin(mut self)
        fn setPixelColor(mut self, i: u16, rgb: u32) as set
        fn show(mut self) blocking(max: 2ms)
        fn numPixels(self) -> u16 as len
    end
end

var strip = NeoPixel(60, 48)
```

- Items: `const NAME: T [as name]`, `let|var Obj: Class [as name]` for C++ globals such as `Wire`,
  `fn name(params) [-> T] [blocking[(max: D)]] [as name]`, and `class Cpp [as Name] do new(...) fn ... end`.
  Methods take `self` or `mut self` first. `pub extern` exports every item to modules that `use` this one.
- Library objects (`ClassTy`) are created only at the top level of a file (`var strip = NeoPixel(...)`),
  never copied, reassigned, stored in structs or arrays, or returned; functions take them by reference
  (`fn paint(mut s: NeoPixel)`), passed by name.
- Binding parameter names only document the C++ signature, so words such as `pin` are allowed there.
- `from lib "…"` adds the library to `lib_deps` in the generated `platformio.ini`. A header that sits
  next to the main file is copied into the generated project's `src/`.
- `as` renames C++ names into Iotift style. Types map as in §4.1; `const char*` ↔ `str`;
  `uint8_t*, size_t` pairs ↔ `[u8]` slices; `std::function` ↔ closures (heap-free when non-capturing).
- `blocking` marks calls that may stall the core. They are allowed in `on boot` / `on ready` and anything
  only they call; elsewhere they are a compile error, found through the whole call graph, unless declared
  `blocking(max: D)` with `D` at most 10ms. `offload` (§7.5) will lift this.
- `unsafe` marks calls whose memory safety the compiler cannot vouch for; they may only be called inside
  `unsafe` blocks, which the `safety` profile forbids or requires a justification comment for.

### 12.3 `iotift bind`

```
iotift bind "adafruit/DHT sensor library" --header DHT.h --out bindings/dht.iot
```

Uses libclang (optional dev dependency, only needed for this command) to read the headers and write an
`extern cpp` file, flagging functions that call `delay()` as `blocking`. The output is reviewed and
committed like any source file.

### 12.4 Raw C++ escape hatch

```iotift
cpp do
    #include <esp_heap_caps.h>
end

fn psram_free() -> u32 => cpp[u32] "heap_caps_get_free_size(MALLOC_CAP_SPIRAM)"

cpp in setup do
    esp_log_level_set("wifi", ESP_LOG_WARN);
end

task x do
    cpp do
        strip.setBrightness($brightness);      -- `$name` refers to an Iotift binding (mangled for you)
    end
end
```

Raw blocks are inserted verbatim with a `#line` directive pointing back to the `.iot` file. They are
forbidden under `safety`, and the build report lists every one.

### 12.5 ESP-IDF access

`use idf "esp_wifi.h"` and `use idf "driver/gpio.h"` work like `extern cpp` with bundled typed bindings
for the most-used IDF headers (`esp_system`, `esp_timer`, `esp_sleep`, `nvs`, `esp_wifi`, `gpio`, `ledc`).

---

## 13. Modules, packages and large projects

### 13.1 Modules

- One file = one module. Paths are relative to the main file's folder: `use drivers.mpu` reads
  `drivers/mpu.iot`.
- Everything is private to its module unless marked `pub`. `pub(pkg)` exposes within the package only
  (planned).

```iotift
use drivers.mpu                     -- mpu.read(), mpu.Accel
use drivers.mpu (Mpu6050, Accel)    -- also brings these names in unqualified
use control as ctl                  -- ctl.step()
use c.math
use pkg "acme/iotift-pid" as pid    -- external Iotift package (planned)
```

- Implemented in `iotift2`: qualified names (`mpu.read()`, `mpu.Accel(...)`, `mpu.LIMIT`), selective
  imports, aliases, `pub` checks with a hint naming the file, modules using modules, and errors that
  name the file they are in. Each module's C++ names get a `<path>__` prefix, so two modules can both
  have a `count` or a `value()`.

- Imports are acyclic between packages; cycles inside a package are allowed for types only.
- Modules that use each other are rejected for now ("move the shared part into a third module").
- No textual inlining (v1 inlines imports). `iotift2` currently emits one `.cpp` for the whole program;
  one `.cpp`/`.hpp` pair per module, for incremental builds, is planned.

### 13.2 The manifest: `iotift.toml`

```toml
[package]
name = "oven-controller"
version = "1.4.0"
edition = "2026"

[target]
chip = "esp32s3"
board = "esp32-s3-devkitc-1"
core = "arduino-3"              # arduino-2 | arduino-3 (later: idf)
flash = "8mb"
psram = true
partitions = "ota"              # default | ota | custom:partitions.csv

[cores]
default = 1
strict = false

[profile.debug]
checks = "all"
log = "debug"

[profile.release]
checks = "proven"               # keep checks the compiler cannot prove away
log = "info"
lto = true

[profile.safety]
inherits = "release"
rules = "safety"

[dependencies]
"acme/iotift-pid" = "^2.0"

[cpp-dependencies]               # passed straight to PlatformIO lib_deps
"adafruit/Adafruit NeoPixel" = "^1.12.3"

[secrets]
WIFI_PASS = { env = "WIFI_PASS" }
```

`iotift build` generates `build/pio/platformio.ini`, `src/`, `include/` and calls PlatformIO.
The generated project is a normal PlatformIO project that can be opened, inspected and debugged on its own.

### 13.3 Project layout

```
oven-controller/
  iotift.toml
  src/
    main.iot            -- entry module (pins, cores, top-level wiring)
    control/pid.iot
    net/telemetry.iot
    drivers/ntc.iot
  bindings/dht.iot      -- generated by `iotift bind`, committed
  tests/
    pid_test.iot
  assets/index.html     -- embedded with file("…")
  .env                  -- secrets, git-ignored
```

### 13.4 Things that matter at scale

- **Interfaces at module boundaries** (traits) so drivers can be swapped for fakes in tests.
- **Compile-time configuration**: `when chip == esp32s3` / `when feature("display")` blocks, with
  features declared in the manifest; unused branches are type-checked but not emitted.
- **Stable generated names** so crash backtraces and map files stay readable.
- **Deterministic builds**: same input and toolchain → byte-identical C++; the manifest lock file pins
  Iotift packages and PlatformIO library versions; `iotift build --sbom` writes a CycloneDX SBOM.

---

## 14. Standard library

Modules marked ★ are in the prelude (no `use` needed).

| Module | Contents |
|--------|----------|
| ★ `std.core` | `print`, `println`, `min`, `max`, `clamp`, `abs`, `now()`, `uptime()`, `panic`, `assert` |
| ★ `std.time` | `Duration`, `Instant`, `Frequency`, deadlines, `Stopwatch` |
| `std.log` | Levelled, tagged logging (`log.info`, `log.warn` …) to Serial / UART / network sink; compile-time level filtering; rate limiting |
| `c.math` | `<cmath>` (§12.1) plus `lerp`, `map_range`, `deg`, `rad` |
| `std.dsp` | Moving average, median, EMA, PID, biquad filters, Kalman (1D) |
| `std.text` | `Text[N]`, formatting, parsing numbers, splitting |
| `std.collections` | `List`, `Ring`, `Map`, `Set`, `Pool` (all fixed capacity) |
| `std.bytes` | Endian read/write, CRC8/16/32, base64, hex |
| `std.json` | `json.decode[T]`/`json.encode` derived from struct definitions at compile time, no heap |
| `std.cbor` | Same for CBOR (compact telemetry) |
| `std.wifi`, `std.net`, `std.http`, `std.mqtt`, `std.ntp`, `std.mdns`, `std.udp`, `std.ws` | §11 |
| `std.ota` | HTTPS OTA with signature verification and rollback (uses the OTA partitions) |
| `std.storage` | NVS key-value (`@nvs` statics), LittleFS files |
| `std.crypto` | SHA-256, HMAC, AES-GCM, ECDSA verify, secure random (mbedTLS / hardware accel) |
| `std.power` | Light/deep sleep, wake sources, CPU frequency, `@rtc` data |
| `std.watchdog` | Task watchdog per core / per task (`task x watchdog 5s`) |
| `std.sys` | Chip info, MAC, reset reason, heap/stack stats, restart |
| `std.ble` | Phase 3: GATT server/client declarations (NimBLE) |
| `std.heap` | Opt-in `String`, `Vec`, `Box` (forbidden in `safety`) |
| `std.test` | Test runner and simulator hooks (host only) |

---

## 15. Safety profiles and compile-time checks

### 15.1 Profiles

| Profile | For | Adds |
|---------|-----|------|
| `beginner` | Learning | Extra hints in errors, generous defaults, `f64` and recursion warnings off |
| `standard` (default) | Most products | Everything below that is cheap |
| `safety` | High-assurance and defence-style projects | No heap, no recursion, no raw `cpp`, no `unsafe`, overflow and bounds checks always on, every `wait` must be bounded (`timeout`) or listed as intentionally unbounded, every `panic` site and every `offload` listed in the build report, all `match` exhaustive without `_` on enums, maximum stack and frame sizes computed and checked, ISR worst-case path analysed |

Rules can be toggled individually (`[profile.x.rules]`) and are named so they can be cited in
justification records (e.g. `IOT-S012 unbounded-wait`).

### 15.2 Hardware checks (all profiles)

- Two declarations on the same GPIO.
- Output on an input-only pin (GPIO 34–39 on the original ESP32).
- Use of flash/PSRAM pins (GPIO 6–11 on ESP32; 26–32 on S3 with octal PSRAM).
- Strapping pins (0, 2, 5, 12, 15 on ESP32) used as outputs with a conflicting initial level: warning.
- **ADC2 pins used while WiFi is declared** on the original ESP32 (ADC2 is unavailable when WiFi is on): error.
- LEDC channel/timer exhaustion and frequency/resolution combinations the clock cannot reach.
- Touch, DAC, RMT, PCNT availability per chip (no DAC on S3/C3, etc.).
- A `core 1` block on a single-core chip with `strict_cores`.

### 15.3 Concurrency checks

- Cross-core access to a non-`shared` global (§8.2).
- `wait` in a `fn`, handler, `lock` block or ISR.
- Blocking extern called from a flow or handler without `offload`.
- Lock-order inversions between `shared` values.
- Flow recursion (frame size must be static).
- A `loop` in a flow without any `wait` on some path.

### 15.4 Build report

`iotift build --report` writes `build/report.html`/`.json`: memory map (static RAM per module, flow frame
sizes, channel buffers, stack sizes with worst-case estimates), timers and their periods per core, every
cross-core channel, every `offload`, `unsafe`, `cpp` and `panic` site, and the pin map with peripherals assigned.

---

## 16. Compilation model and generated code

### 16.1 Pipeline

```
.iot files ─► lexer (tokens, NEWLINE only where a statement can end, units folded into literals)
           ─► parser (AST, error recovery, all errors reported, any error is fatal)
           ─► resolver (modules, `use`, packages, extern bindings)
           ─► semantic (types, generics, traits, units, errors/optionals)
           ─► effects (core ownership, may-wait, may-block, ISR-safety, lock order)
           ─► lowering (flows → state machines, events → executor registrations, `match` → switch)
           ─► codegen (one .cpp/.hpp per module + main.cpp + platformio.ini)
           ─► PlatformIO (g++ for Xtensa / RISC-V)
```

This keeps today's Python structure (`lexer.py`, `parser.py`, `semantic.py`, `codegen.py`) and adds two
passes: an **effects** pass and a **lowering** pass. The current rule "never emit a placeholder; raise
`CodeGenError` instead" stays.

### 16.2 What a flow becomes

```iotift
task blink do
    loop do
        led.on()
        wait 200ms
        led.off()
        wait 800ms
    end
end
```

compiles to this (actual `iotift2` output, trimmed; `#line` directives omitted):

```cpp
struct iot_task_blink_frame {
  int32_t st = 0;        // resume point; -1 when finished
  uint32_t wake = 0;     // millis() at which to resume
};
static iot_task_blink_frame iot_task_blink;
static void iot_task_blink_step() {
  auto &f = iot_task_blink;
  switch (f.st) {
  case 0:;
    for (;;) {
      digitalWrite(led_, HIGH);
      f.wake = millis() + iot::ms(INT64_C(200000));
      f.st = 1;
      return;
  case 1:;
      digitalWrite(led_, LOW);
      f.wake = millis() + iot::ms(INT64_C(800000));
      f.st = 2;
      return;
  case 2:;
    }
    f.st = -1;
    return;
  default:
    return;
  }
}
```

Every local a task uses is a member of its frame struct, so nothing lives on a stack across a `wait`
and jumping back in at a `case` label never skips an initialisation. `match` inside a task is
generated as an `if`/`else` chain so its arms cannot capture the task's resume labels.

C++20 coroutines are deliberately **not** used: their frames are heap-allocated by default and need
GCC ≥ 10 flags that vary across Arduino-ESP32 versions. The explicit state machine is predictable,
debuggable and works on core 2.x (GCC 8) and 3.x.

### 16.3 Runtime library

A header-only `iotift_rt` (shipped with the compiler, copied into `include/`): executor, timer heap,
channels, signals, shared values, `Opt`/`Res`, `Text`, fixed collections, panic handler, pin/LEDC/RMT
allocators (the existing LEDC allocator that handles core 2.x/3.x moves here). Size target: under 8 KB
flash for a blink program, under 1 KB static RAM for the runtime itself.

### 16.4 Source mapping

Every generated statement carries a `#line N "src/file.iot"` directive, so compiler errors in bindings,
`addr2line` backtraces and GDB step through `.iot` source. `iotift monitor` decodes panics and
exception backtraces into `.iot` file:line automatically.

### 16.5 Testing

- `iotift test` compiles the program with the **host simulator** (the existing `tests/sim` fake Arduino
  core grows into `iotift_sim`), runs `test` blocks, and supports simulated time:

```iotift
test "debounced press toggles the relay" do
    sim.press(button, for: 50ms)
    sim.advance(10ms)
    expect relay.is_on()
end
```

- `iotift test --hw` runs the same tests on a connected board with results over Serial.

---

## 17. Migration from Iotift v1 and roadmap

### 17.1 What stays the same

The concepts already in v1 carry over: `pin` declarations, `every`/`after`/`on` events, named timers,
`isr` safety rules, first-class `wifi` blocks with events and retry policies, the min-heap scheduler,
`extern` functions, raw C blocks, the formatter's "never change meaning" guarantee, the LSP, and
behaviour testing against the simulator.

### 17.2 What changes

| v1 | v2 | Why |
|----|----|-----|
| `int x = 0;` | `var x = 0` / `var x: i32 = 0` | Not C-like; sized types |
| `{ }` and `;` | `do … end`, newlines | Distinct, beginner-friendly, survives copy-paste |
| `//` comments | `--` | Distinct |
| `fn f(int a) -> int` | `fn f(a: i32) -> i32` | Name-first reads better and parses unambiguously |
| `LED = 1;` / `LED = 0 after 200;` | `led.on()` / `after 200ms => led.off()` | Explicit actions |
| `every 1000` (ms) | `every 1s` | Units required |
| `on BTN.press` polled in `loop()` | Interrupt-driven, dispatched by executor | Nothing missed |
| `tick { }` | `on ready` | Clearer name |
| `str` = `const char*` | `str` view + `Text[N]` | Real strings without heap |
| Imports inlined textually | Separate compilation per module | Large projects |
| `c header {}` / `c global {}` | `extern cpp`, `use c.*`, `cpp` blocks | Typed interop |
| `&&`, `\|\|`, `!` | `and`, `or`, `not` | Readability |
| C precedence for `&` vs `==` | Bitwise binds tighter | Removes a classic bug |

`iotift migrate file.iot` rewrites v1 source to v2 (the v1 parser already produces an AST; the v2
formatter prints it). v1 files are still accepted with `edition = "2025"` during the transition.

### 17.3 Roadmap

| Phase | Scope |
|-------|-------|
| **1. Core language** | `do … end` lexer and parser, types with sized ints, structs/enums/match, `fn`, modules with `pub`, `iotift.toml`, separate compilation, `#line` mapping, `migrate` |
| **2. Non-blocking + cores** | `flow`/`task`/`wait`/`select`/`start`, executor runtime, `core` blocks, ownership analysis, `chan`/`signal`/`shared`, interrupt-driven pin events, `offload` |
| **3. Interop** | `extern cpp` with `blocking`, `use c.*` bundled bindings, `iotift bind`, ESP-IDF bindings |
| **4. Networking** | WiFi (port of milestone 8), HTTP client/server, MQTT, NTP, OTA, mDNS, JSON |
| **5. Large-project & assurance** | Traits/generics, packages, `safety` profile, build report, SBOM, `iotift test` with simulated time |
| **6. Extras** | BLE, ESP-NOW, ULP, display/UI module |

### 17.4 Implementation status (`iotift2` 2.0.0a2)

The first v2 compiler lives in `iotift-v2/` next to the v1 project and leaves the v1 compiler untouched.
It already covers part of phase 2, because tasks, `wait` and cores are what make the language different.

| Area | Implemented now | Not yet |
|------|-----------------|---------|
| Syntax | `do … end` and `=>` blocks, `elif`/`else`, newline-terminated statements, `--`/`{- -}` comments, contextual `by`/`offset`/`timeout`/`until` | doc comments in tooling |
| Types | `i8`…`u64`, `usize`, `f32`/`f64`, `bool`, `str`, `Duration`, `Frequency`, `[T; N]`, structs with defaults, methods (`self`, `mut self`, static), unit enums, untyped literals that take their type from context, lossless widening only | optionals, results, `Text[N]`, traits, generics, enum payloads, overflow checks |
| Statements | `let`/`var`, `if`/`elif`/`else`, `while`, `for … in a..b by n`, `for x in array`, `loop`, exhaustive `match` with `\|`, ranges and guards, `break`/`continue`/`return` | labelled loops, `defer` |
| Non-blocking | `task` with `wait <duration>` and `wait until <cond> timeout <duration>` as frame-based state machines; `every … offset … as name` with `start`/`stop`/`running`; `after`; `on pin.press/release/rising/falling/change` (debounced); `on boot`/`on ready` | `flow`, `select`, `start`, channels, signals, `offload` |
| Cores | `core 0/1 as name do … end`, `task … on core N`, ownership analysis through function calls, `shared var` (atomic `get`/`set`/`add`), a pinned FreeRTOS executor for core 0 | `lock`, `watch`, per-core budgets |
| Hardware | `out` (active low, initial), `in` (pull, debounce), `adc` (atten, `read`, `read_mv`), `pwm` (freq, bits, `duty`, `write`) on core 2.x and 3.x; chip-aware pin checks for ESP32/S2/S3/C3/C6/H2 | buses, RMT, PCNT, touch, DAC, interrupts |
| WiFi | `wifi name do … end` station and access point, `connected`/`lost`/`failed`/`client_joined`/`client_left` events, `state`/`connected`/`ip`/`rssi`/`mac`/`clients`, `connect()`/`disconnect()`, `retry: none / forever / fixed / backoff`, `secret("NAME")` from the environment or `.env`, ADC2 and H2 checks (§11.1) | scan, static IP, NVS credentials, provisioning, HTTP, MQTT, NTP, OTA |
| Interop | `use c.math` with typed `<cmath>`; `extern cpp` with constants, global objects, functions, classes, default arguments, `blocking` checks and `lib_deps` (§12.2) | `iotift bind`, `cpp` blocks, slices and callbacks in bindings |
| Modules | `use a.b`, `use a.b (X, y)`, `as`, `pub`, per-module C++ prefixes, cycle and missing-file errors (§13.1) | packages, `iotift.toml`, one `.cpp` per module |
| Tooling | `python -m iotift2` with `check`, `build`, `project` (adds `lib_deps`, copies local headers), `flash`, `sim`, `#line` mapping to each `.iot` file, errors with a fix hint | formatter, LSP |

Pin events are debounced by polling from the owning core's executor every millisecond (as in v1);
moving to GPIO interrupts is planned.

Each phase ends with every example compiling and runtime tests in the simulator, as the current
`CLAUDE.md` rules require.

---

## 18. Decisions and suggested additions

### Decisions (2026-10-13)

1. **Blocks are `do … end`**, not indentation and not braces. One-statement bodies use `=>`.
2. **Comments use `--`** (`---` for doc comments, `{- -}` for blocks).
3. **Resumable functions are `flow`**, and `wait` is the single keyword for sleeping, events, channels and calls.
4. **Handlers do not `wait`.** An explicit opt-in (`on button.press flow`) that starts a flow per event is planned.
5. **Default core is 1** for user code; beginners never see cores until they write a `core` block.
6. **Overflow checks stay on where the compiler cannot prove safety** (the `proven` setting), to be measured
   on real code before release builds are finalised.
7. **Arduino-ESP32 is the backend**, with the executor kept portable to a later ESP-IDF backend.
8. **Migration is a `migrate` tool plus one transition edition** for v1 files.
9. **The name stays Iotift.**

### Suggested additions (not scheduled yet)

1. **Simulated time in tests** (`sim.advance`), built on the existing `tests/sim` core. This is the
   biggest productivity win for non-blocking code.
2. **State machines as a language construct**, since most firmware is one:
   ```iotift
   machine door do
       state closed do
           on open_btn.press => go opening
       end
       state opening do
           enter => motor.forward()
           after 5s => go fault
           on limit_open.press => go open
       end
   end
   ```
3. **Units of measure beyond time**: `°C`, `V`, `mA`, `%`, so `read_mv()` returns `Volts` and mixing them up
   is a type error.
4. **Built-in telemetry**: `telemetry to broker every 10s => temp, setpoint, rssi` generating the JSON/CBOR,
   MQTT topic and rate limiting.
5. **Secure-by-default networking**: TLS on, certificate pinning syntax, signed OTA with rollback,
   flash encryption and secure boot settings in the manifest.
6. **Power-aware scheduling**: because the executor knows every deadline, it can enter light sleep
   automatically between them (`power: auto` in the manifest).
7. **A `--explain` flag** for every error code (like `rustc --explain`), which helps beginners most.
8. **Record/replay**: log every external event (pins, network, timers) to a ring buffer so a field
   failure can be replayed in the simulator.

---

## Appendix A: grammar sketch

Informal EBNF. `NL` is a newline the lexer kept (one that can end a statement). `block` is the
universal `do … end`.

```ebnf
module      = { use_decl | item } ;
use_decl    = "use" path [ ".{" ident { "," ident } "}" ] [ "as" ident ] NL ;
item        = [ attrs ] [ "pub" ] ( const | global | fn_decl | flow_decl | task_decl
            | struct_decl | enum_decl | trait_decl | impl_decl | pin_decl | bus_decl
            | device_decl | periph_decl | core_block | event | extern_cpp | cpp_block | test_decl ) ;

global      = ( "let" | "var" | "shared" "var" | "static" "var" ) ident [ ":" type ] [ "=" expr ] [ attrs ] NL ;
const       = "const" ident [ ":" type ] "=" expr NL ;

fn_decl     = "fn" ident [ generics ] "(" [ params ] ")" [ "->" ret ] body ;
flow_decl   = "flow" ident [ generics ] "(" [ params ] ")" [ "->" ret ] body ;
task_decl   = "task" ident [ "on" "core" core_id ] [ task_opts ] body ;
params      = param { "," param } ;
param       = [ "mut" ] ident ":" type [ "=" expr ] | [ "mut" ] "self" ;
ret         = type [ "!" [ type ] ] ;
block       = "do" { stmt } "end" ;
body        = "=>" ( expr | stmt ) | block ;

struct_decl = "struct" ident [ generics ] "do" { ident ":" type [ "=" expr ] ( NL | "," ) } "end" ;
enum_decl   = "enum" ident [ generics ] "do" { ident [ "(" params ")" ] ( NL | "," ) } "end" ;
trait_decl  = "trait" ident "do" { fn_sig | fn_decl } "end" ;
impl_decl   = "impl" [ generics ] type [ "for" type ] "do" { fn_decl | flow_decl } "end" ;

core_block  = "core" ( INT | "any" ) [ "as" ident ] "do" { core_opt | item } "end" ;
event       = ( "every" expr [ "offset" expr ] [ "precise" ] [ "as" ident ]
              | "after" expr
              | [ "isr" ] "on" event_src [ "|" ident { "," ident } "|" ] ) body ;

pin_decl    = "pin" ident "=" pin_kind expr { "," pin_opt } NL ;
pin_kind    = "out" | "in" | "adc" | "pwm" | "touch" | "dac" ;

stmt        = let | var | assign | expr_stmt | if | while | for | loop | match | select
            | "wait" expr [ "timeout" expr ] | "return" [ expr ] | "fail" expr
            | "break" [ ident ] | "continue" [ ident ] | "pass" | "defer" stmt
            | "lock" ident "as" ident body | cpp_block ;

if          = "if" expr ( "=>" stmt
              | "do" { stmt } { "elif" expr "do" { stmt } } [ "else" { stmt } ] "end" ) ;
for         = "for" pattern "in" expr [ "by" expr ] body ;
match       = "match" expr "do" { pattern { "|" pattern } [ "if" expr ] ( "=>" stmt | block ) NL } "end" ;
select      = "select" "do" { [ ident "=" ] "wait" expr ( "=>" stmt | block ) NL } "end" ;

type        = path [ "[" type_args "]" ] | "[" type [ ";" expr ] "]" | type "?" | "(" type { "," type } ")" ;
```

## Appendix B: keyword list

Reserved: `and as after attach break bus const continue core cpp defer device do dyn elif else end enum every
extern fail false flow fn for if impl in isr let lock loop match mut not on or pass pin pub return
select self shared start static struct task test then trait true try use var wait when while`

Contextual (only keywords in their declaration context): `out in adc pwm touch dac pull up down
active low high initial debounce atten freq bits by offset precise timeout until budget priority thread
offload blocking unsafe wifi http server mqtt ntp ota udp ws mdns ble machine state go enter`
