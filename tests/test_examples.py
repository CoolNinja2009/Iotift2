"""Every example must compile, build against the simulator and behave."""

import os
from pathlib import Path

import pytest

from helpers import run, texts, pin_changes, CXX


@pytest.fixture(autouse=True)
def wifi_secret(monkeypatch):
    if 'WIFI_PASS' not in os.environ:
        monkeypatch.setenv('WIFI_PASS', 'example-pass')   # weather_station reads secret("WIFI_PASS")


ROOT = Path(__file__).resolve().parent.parent / 'examples'
# single-file examples, plus the main file of each multi-file example folder
EXAMPLES = sorted(ROOT.glob('*.iot')) + sorted(ROOT.glob('*/main.iot'))


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
@pytest.mark.parametrize('path', EXAMPLES, ids=lambda p: str(p.relative_to(ROOT)))
def test_example_builds_and_runs(path):
    run(path.read_text(), 100, path=path)
    run(path.read_text(), 100, path=path, flags=('-DESP_ARDUINO_VERSION_MAJOR=3',))


def src(name):
    return (Path(__file__).resolve().parent.parent / 'examples' / name).read_text()


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_blink_example():
    assert [t for t, _ in pin_changes(run(src('blink.iot'), 1600), 2)] == [500, 1000, 1500]


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_two_cores_example():
    assert texts(run(src('two_cores.iot'), 2100)) == ['report 1: 100 samples', 'report 2: 200 samples']


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_traffic_light_button_shortens_green():
    lines = run(src('traffic_light.iot'), 6000, '2000:4:0,2100:4:1')
    # green on at boot, yellow after the debounced press at 2025ms, red 3s later
    assert pin_changes(lines, 27)[:2] == [(0, 1), (2025, 0)]
    assert (2025, 1) in pin_changes(lines, 26)
    assert (5025, 1) in pin_changes(lines, 25)


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_thermostat_heats_when_cold():
    lines = run(src('thermostat.iot'), 5100, '0:A34:3000')
    out = '\n'.join(lines)
    assert 'ledcWrite(ch0,1023)' in out          # far below target: full power
    assert (200, 1) in pin_changes(lines, 2)       # status LED: too cold
    assert texts(lines)[-1].endswith('(target 18.0, mode eco)')


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_greenhouse_waters_dry_soil_and_shows_wifi_state():
    path = ROOT / 'greenhouse' / 'main.iot'
    # dry soil (raw 3800 = 3062 mV) until 3s, then wet (raw 1000 = 805 mV); WiFi up at 1s, lost at 4.5s
    lines = run(path.read_text(), 6100, '0:A34:3800,1000:W:got_ip,3000:A34:1000,4500:W:lost',
                path=path)
    assert texts(lines) == ['online at 192.168.1.50', 'soil 0% pump true', 'soil 100% pump false',
                            'offline, reconnecting', 'soil 100% pump false']
    assert pin_changes(lines, 26) == [(2000, 1), (4000, 0)]
    shows = [ln.split('NEOPIXEL ')[1] for ln in lines if 'NEOPIXEL ' in ln and 'begin' not in ln
             and 'brightness' not in ln]
    assert shows == ['ff6400', '00a000', '0000ff']


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_weather_station_sensor_oled_and_wifi():
    path = ROOT / 'weather_station' / 'main.iot'
    # WiFi up at 1s; button presses at 3s and 5s (pages: now -> range -> network); WiFi lost at 6s
    lines = run(path.read_text(), 8100, '1000:W:got_ip,3000:0:0,3100:0:1,5000:0:0,5100:0:1,6000:W:lost',
                path=path)
    out = texts(lines)
    # the bus scan prints its own lines before the summary line that called it
    assert out[:3] == ['i2c: device at 0x48', 'i2c: device at 0x68', 'i2c: 2 device(s)']
    assert 'wifi: up, ip 192.168.1.50, rssi -50' in out
    assert 'wifi: lost, retrying' in out
    oled = [l.split('OLED ', 1)[1] for l in lines if '] OLED ' in l and 'begin' not in l]
    assert oled[0] == 'Now | 22.0 C | 42 % | 993.2 hPa'
    assert 'Today | min 22.0 | max 22.0 | samples 1' in oled
    assert 'WiFi | online | reconnects 0' in oled
    assert oled[-1] == 'WiFi | offline | reconnects 1'


@pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')
def test_imu_logger_access_point_and_uplink_task():
    lines = run(src('imu_logger.iot'), 24100, '500:W:join,2000:W:join,3000:W:leave,22000:W:got_ip,24000:W:lost')
    out = texts(lines)
    assert out[0] == 'mpu6050 ready'
    assert any('WiFi.softAP(Logger-Setup,logger1234,ch6,max2)' in l for l in lines)
    assert [t for t in out if t.startswith('ap:')] == [
        'ap: client joined (1 connected)', 'ap: client joined (2 connected)', 'ap: client left (1 connected)']
    assert [t for t in out if t.startswith('uplink:')] == [
        'uplink: no network after 20s, still logging locally', 'uplink: online as 192.168.1.50', 'uplink: dropped']
    assert 'accel avg 32120 alarms 0' in out     # a steady sensor never trips the alarm
