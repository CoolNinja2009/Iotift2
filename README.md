# Iotift 2 compiler (2.0.0a2)

The first compiler for the Iotift 2 language described in
[`docs/iotift-language-spec.md`](docs/iotift-language-spec.md).
It turns `.iot` source into one Arduino-ESP32 C++ file or a ready-to-build PlatformIO project.
It is separate from the v1 compiler in `../Iotift/`, which it does not change.

```iotift
pin led = out 2
pin button = in 0, pull up, debounce 30ms

var presses: u32 = 0

task blink do
    loop do
        led.on()
        wait 100ms          -- pauses this task only
        led.off()
        wait 900ms
    end
end

on button.press do
    presses += 1
    println("pressed {presses} times")
end
```

## Install

**Windows** — open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/CoolNinja2009/Iotift2/main/install.ps1 | iex
```

**macOS / Linux**:

```sh
curl -fsSL https://raw.githubusercontent.com/CoolNinja2009/Iotift2/main/install.sh | sh
```

The installer needs no admin rights. It finds Python 3.8+ (on Windows it installs Python with winget
if there is none), installs Iotift into its own environment, puts the `iotift` command on your PATH
and runs `iotift setup`, which installs PlatformIO and downloads the ESP32 toolchain once.
Set `IOTIFT_SKIP_SETUP=1` to skip that last step, or `IOTIFT_VERSION=2.0.0` to pick a release.

**With pip** (any OS, Python 3.8+): download the `.whl` from the
[latest release](https://github.com/CoolNinja2009/Iotift2/releases) and run
`pip install iotift2-*.whl`, or install straight from the source:
`pip install https://github.com/CoolNinja2009/Iotift2/archive/refs/heads/main.zip`.

You do not need to install PlatformIO yourself: the first `iotift upload` installs it if it is
missing (it asks first in a terminal; `--yes` skips the question, `IOTIFT_NO_AUTO_INSTALL=1` turns it off).

To uninstall, delete `%LOCALAPPDATA%\Iotift` (Windows) or `~/.local/share/iotift` and
`~/.local/bin/iotift*` (macOS, Linux).

## Commands

```
iotift setup                         # install PlatformIO + the ESP32 toolchain, look for boards
iotift check  app.iot                # type-check only
iotift build  app.iot -o app.cpp     # C++ for Arduino-ESP32 (core 2.x or 3.x)
iotift project app.iot -d app_pio    # platformio.ini + src/main.cpp
iotift upload app.iot                # project, build, upload to the connected board
iotift upload app.iot --port COM5 --monitor
iotift sim    app.iot --ms 3000 --events "500:0:0,600:0:1"
```

Without installing, run the same commands from a checkout as `python -m iotift2 ...`.

`upload` (alias `flash`) finds the board by its USB serial chip (CP210x, CH340/CH9102, FTDI or
Espressif native USB); pass `--port` when none or several are connected. It also accepts a `.cpp`
file from `build`. It uses PlatformIO, installing it first if neither the `pio` command nor the VS Code
extension's copy is found.

`sim` builds the program against the simulated ESP32 core in `iotift2/sim` (needs `g++` or `clang++`)
and prints pin changes and Serial output with simulated milliseconds. Events are
`<ms>:<gpio>:<0|1>` for digital inputs, `<ms>:A<gpio>:<value>` for ADC readings and
`<ms>:W:<got_ip|lost|join|leave>` for WiFi. Headers next to the program and stand-ins in
`iotift2/sim/libs` (NeoPixel, BME280 and SSD1306 stand-ins are included) are on the simulator's include path.

## What works today

- `do … end` blocks everywhere, `=>` for one-statement bodies, `elif`/`else`, `--` comments.
- Sized integers, `f32`/`f64`, `bool`, `str`, `Duration` (`500ms`, `1.5s`), `Frequency` (`25kHz`),
  fixed arrays `[T; N]`, structs with defaults and methods, enums, exhaustive `match`.
- Non-blocking by construction: `task` + `wait 200ms` / `wait until cond timeout 5s`, compiled to
  state machines with no heap and no per-task stack. `wait` anywhere else is a compile error.
- `every … offset … as name`, `after`, `on pin.press/release/rising/falling/change`, `on boot`, `on ready`.
- Cores: `core 0 as network do … end`, `task t on core 0`, `shared var`. The compiler rejects any code
  that touches another core's variables, including through function calls.
- Pins: `out`, `in`, `adc`, `pwm`, checked against the chip's pin map (input-only, flash, ADC, LEDC
  clock limits, duplicates) for the ESP32, S2, S3, C3, C6 and H2.
- `use c.math` for typed `<cmath>`; `abs`, `min`, `max`, `clamp`, `sqrt`, `sin`, `cos` … without imports.
- Multi-file programs: `use drivers.soil` reads `drivers/soil.iot` next to the main file; `pub` exports,
  `use drivers.soil (moisture)` imports names, `as` renames.
- Any Arduino C++ library through `extern cpp "Header.h" from lib "owner/Name@^1.0" do … end`:
  constants, functions, classes with constructors and methods. The library lands in `lib_deps`.
  Calls marked `blocking` are only allowed in `on boot`/`on ready` (or with `blocking(max: 2ms)`).
- First-class WiFi: `wifi home do ssid: … password: secret("WIFI_PASS") retry: backoff(1s, max: 1min) end`,
  `on home.connected`/`lost`, `home.ip`, `home.state`, an access point mode, and secrets read from the
  environment or a `.env` file at build time.
- Errors name the line and column and say how to fix the problem.

See section 17.4 of the spec for what is not implemented yet.

## Layout

| File | Role |
|------|------|
| `iotift2/lexer.py` | Source → tokens (units folded into literals, newlines kept only where a statement can end) |
| `iotift2/parser.py` | Tokens → AST (`nodes.py`); all parse errors are reported together and are fatal |
| `iotift2/checker.py` | Names, types, units, mutability, `wait` rules, pin rules, core ownership |
| `iotift2/typesys.py`, `chips.py` | Type system and per-chip pin maps |
| `iotift2/codegen.py`, `runtime.py` | AST → C++ with a small runtime (`namespace iot`) and one executor per core |
| `iotift2/cli.py` | The `python -m iotift2` command |
| `iotift2/compiler.py` | Loads the main file and every module it uses, then checks and generates |
| `iotift2/sim/` | The simulated ESP32 core used by `sim` and the tests |
| `install.ps1`, `install.sh` | One-line installers for Windows and macOS/Linux |
| `.github/workflows/` | CI (tests, package, installers, real PlatformIO builds) and the tag-triggered release |
| `tests/` | 143 tests; most build the generated C++ and run it in the simulator |
| `examples/` | Example programs (`greenhouse/` and `weather_station/` are multi-file ones with WiFi and C++ libraries; `weather_station/` drives a BME280 and an SSD1306 OLED, `imu_logger.iot` reads an MPU6050 over raw I2C with a WiFi access point); every one is built and run by the tests for core 2.x and 3.x |

```
python -m pytest tests/
```

## Releasing

Set `__version__` in `iotift2/__init__.py`, then push a matching tag:

```
git tag v2.0.0 && git push origin v2.0.0
```

The release workflow runs the tests, builds the wheel and source archive, and publishes a GitHub
release with them and the installers attached (versions with `a`, `b` or `rc` are marked
pre-release). The installers always pick the newest release. Publishing to PyPI is optional:
set the repository variable `PYPI_PUBLISH` to `true` and register the repository as a trusted
publisher for `iotift2` on pypi.org.

## Not yet verified on hardware

Every example compiles with the real toolchain (PlatformIO 6.2, `espressif32`, Arduino core,
`esp32dev`), including `greenhouse/` with WiFi and a C++ library, and runs against the simulated core
with `-Wall -Wextra` for core 2.x and 3.x. None has been flashed to a board yet:
`python -m iotift2 upload examples/blink.iot` with an ESP32 plugged in is the next check.
