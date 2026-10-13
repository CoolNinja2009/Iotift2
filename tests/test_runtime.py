"""Behaviour tests: compile, build against the simulated ESP32 core and run.

Each simulated loop() call advances the clock by 1 ms.
"""

import pytest

from helpers import run, texts, serial, pin_changes, CXX

pytestmark = pytest.mark.skipif(CXX is None, reason='needs g++ or clang++')


def test_blink_with_every():
    lines = run('pin led = out 2\nevery 100ms => led.toggle()\n', 450)
    assert pin_changes(lines, 2) == [(100, 1), (200, 0), (300, 1), (400, 0)]


def test_task_waits_do_not_block_timers():
    src = '''
pin led = out 2
pin buzzer = out 4
task blink do
    loop do
        led.on()
        wait 50ms
        led.off()
        wait 150ms
    end
end
every 30ms => buzzer.toggle()
'''
    lines = run(src, 420)
    assert pin_changes(lines, 2) == [(0, 1), (50, 0), (200, 1), (250, 0), (400, 1)]
    assert [t for t, _ in pin_changes(lines, 4)][:4] == [30, 60, 90, 120]


def test_task_locals_survive_waits_and_task_can_finish():
    src = '''
task count do
    var total: i32 = 0
    for i in 1..=4 do
        total += i
        println("i={i} total={total}")
        wait 10ms
    end
    println("done {total}")
end
'''
    assert serial(run(src, 100)) == [(0, 'i=1 total=1'), (10, 'i=2 total=3'), (20, 'i=3 total=6'),
                                     (30, 'i=4 total=10'), (40, 'done 10')]


def test_wait_until_with_timeout():
    src = '''
pin door = in 5, pull up, debounce 0ms
task t do
    wait until door.is_low() timeout 100ms
    println("first at {uptime()}")
    wait until door.is_low() timeout 1s
    println("second at {uptime()}")
end
'''
    out = serial(run(src, 400, '250:5:0'))
    assert out == [(100, 'first at 100ms'), (250, 'second at 250ms')]


def test_button_press_is_debounced():
    src = '''
pin button = in 4, pull up, debounce 20ms
var presses: u32 = 0
on button.press do
    presses += 1
    println("press {presses}")
end
on button.release => println("release")
'''
    # a bouncy press at 100ms, a clean release at 200ms
    events = '100:4:0,103:4:1,105:4:0,200:4:1'
    assert serial(run(src, 300, events)) == [(125, 'press 1'), (220, 'release')]


def test_after_and_named_timer_stop_start():
    src = '''
pin led = out 2
every 100ms as blink => led.toggle()
after 250ms => blink.stop()
after 600ms do
    println("running={blink.running}")
    blink.start()
end
'''
    lines = run(src, 800)
    assert pin_changes(lines, 2) == [(100, 1), (200, 0), (700, 1)]
    assert texts(lines) == ['running=false']


def test_timer_offset():
    lines = run('pin led = out 2\nevery 100ms offset 10ms => led.toggle()\n', 250)
    assert [t for t, _ in pin_changes(lines, 2)] == [10, 110, 210]


def test_on_boot_and_ready_order():
    src = 'on ready => println("ready")\non boot => println("boot")\n'
    assert texts(run(src, 5)) == ['boot', 'ready']


def test_structs_methods_and_defaults():
    src = '''
struct Reading do
    id: u16
    celsius: f32 = 20.0
end
impl Reading do
    fn fahrenheit(self) -> f32 => self.celsius * 1.8 + 32.0
    fn warm(mut self, by: f32 = 1.0) do
        self.celsius += by
    end
    fn zero() -> Reading => Reading(id: 0, celsius: 0.0)
end
on boot do
    var r = Reading(id: 7)
    r.warm()
    r.warm(by: 3.0)
    let z = Reading.zero()
    println("{r.id} {r.celsius:.1} {r.fahrenheit():.1} {z.celsius}")
end
'''
    assert texts(run(src, 5)) == ['7 24.0 75.2 0.00']


def test_enums_and_match():
    src = '''
enum Mode do
    off, heat, cool
end
fn describe(m: Mode, t: i32) -> i32 do
    match m do
        Mode.off => return 0
        heat if t < 18 => return 1
        heat => return 2
        cool => return 3
    end
end
fn bucket(x: i32) -> i32 do
    match x do
        0 => return 0
        1..=9 => return 1
        10 | 20 | 30 => return 2
        _ => return 9
    end
end
on boot do
    println("{describe(Mode.off, 0)} {describe(Mode.heat, 10)} {describe(Mode.heat, 25)} {describe(Mode.cool, 0)}")
    println("{bucket(0)} {bucket(5)} {bucket(20)} {bucket(11)} {Mode.cool}")
end
'''
    assert texts(run(src, 5)) == ['0 1 2 3', '0 1 2 9 cool']


def test_match_inside_task_across_waits():
    src = '''
enum Step do
    a, b
end
task t do
    var s = Step.a
    loop do
        match s do
            a do
                println("a")
                wait 10ms
                s = Step.b
            end
            b do
                println("b")
                return
            end
        end
    end
end
'''
    assert serial(run(src, 50)) == [(0, 'a'), (10, 'b')]


def test_arrays_loops_and_bounds_panic():
    src = '''
var xs: [i32; 4] = [3, 1, 4, 1]
fn total() -> i32 do
    var s = 0
    for x in xs do
        s += x
    end
    return s
end
on boot do
    println("sum={total()} len={xs.len()}")
    var i = 0
    while i < 10 do
        xs[i] = i
        i += 1
    end
end
'''
    lines = run(src, 5, expect_code=3)
    t = texts(lines)
    assert t[0] == 'sum=9 len=4'
    assert 'PANIC: index 4 is out of range for an array of 4 (test.iot:14)' in t


def test_print_formats():
    src = '''
on boot do
    let x: u8 = 255
    let f = 3.14159
    println("{x} {x:x} {x:X} {x:b} {f} {f:.3} {true} {1500ms} {250us} {5kHz} {-42}")
end
'''
    assert texts(run(src, 5)) == ['255 ff FF 11111111 3.14 3.142 true 1500ms 250us 5000Hz -42']


def test_math_module_and_prelude():
    src = '''
use c.math
on boot do
    println("{math.atan2(1.0, 1.0) * 4.0:.4} {sqrt(16.0):.1} {math.hypot(3.0, 4.0):.1}")
    println("{abs(-5)} {min(3, 7)} {max(2.5, 1.5):.1} {clamp(15, 0, 10)} {math.pi:.5}")
    println("{math.map_range(5.0, 0.0, 10.0, 0.0, 100.0):.1} {math.deg(math.pi):.1}")
end
'''
    assert texts(run(src, 5)) == ['3.1416 4.0 5.0', '5 3 2.5 10 3.14159', '50.0 180.0']


def test_pwm_core2_and_core3():
    src = '''
pin fan = pwm 13, freq 25kHz, bits 10
pin led = pwm 14, freq 5kHz
on boot do
    fan.duty(0.5)
    led.write(300)
    println("{fan.max()} {led.max()}")
end
'''
    lines = run(src, 5)
    joined = '\n'.join(lines)
    assert 'ledcSetup(ch0,25000,10)' in joined and 'ledcSetup(ch2,5000,8)' in joined
    assert 'ledcWrite(ch0,512)' in joined and 'ledcWrite(ch2,255)' in joined
    assert 'LEDC CLASH' not in joined
    assert texts(lines) == ['1023 255']
    lines3 = '\n'.join(run(src, 5, flags=('-DESP_ARDUINO_VERSION_MAJOR=3',)))
    assert 'ledcAttachChannel(13,25000,10,ch0)' in lines3
    assert 'ledcWrite(pin13,512)' in lines3 and 'ledcWrite(pin14,255)' in lines3


def test_adc_and_active_low_output():
    src = '''
pin probe = adc 34
pin relay = out 26, active low
every 10ms do
    if probe.read() > 2000 do
        relay.on()
    else
        relay.off()
    end
end
on boot => println("mv={probe.read_mv()}")
'''
    lines = run(src, 40, '15:A34:3000,25:A34:100')
    assert pin_changes(lines, 26) == [(0, 1), (20, 0), (30, 1)]


def test_two_cores_with_shared_value():
    src = '''
shared var hits: u32 = 0
core 1 as control do
    every 10ms => hits.add(1)
end
core 0 as network do
    var reports: u32 = 0
    task reporter do
        loop do
            wait 50ms
            reports += 1
            println("report {reports}: {hits.get()}")
        end
    end
end
'''
    assert texts(run(src, 160)) == ['report 1: 5', 'report 2: 10', 'report 3: 15']


def test_durations_arithmetic():
    src = '''
fn backoff(attempt: i32) -> Duration => 100ms * attempt + 50ms
task t do
    for n in 1..=3 do
        wait backoff(n)
        println("{n} at {uptime().as_ms()}")
    end
end
'''
    assert texts(run(src, 1000)) == ['1 at 150', '2 at 400', '3 at 750']


def test_constants_and_wide_literals():
    src = '''
const BIG: u64 = 1 << 40
const RATIO = 2.5
const N = 3
var buf: [u8; N * 2]
on boot do
    let half: f64 = RATIO / 2.0
    println("{BIG} {RATIO} {half:.2} {buf.len()}")
end
'''
    assert texts(run(src, 5)) == ['1099511627776 2.50 1.25 6']


def test_task_loop_continues_after_wait_in_either_branch():
    # A `wait` as the last statement of both branches of an if/else at the end of a loop body:
    # after resuming in either branch the loop must go round again, not end the task.
    src = '''
pin led = out 2
pin mode = in 4, pull up
task heartbeat do
    loop do
        led.on()
        wait 50ms
        led.off()
        if mode.is_high() do
            wait 450ms
        else
            wait 150ms
        end
    end
end
'''
    lines = run(src, 5000, '2500:4:0')
    rises = [t for t, v in pin_changes(lines, 2) if v == 1]
    # 500 ms period while the input is high; it is low from 2.5 s, so from the 2550 ms check on
    # the period is 200 ms
    assert rises == [0, 500, 1000, 1500, 2000, 2500] + list(range(2700, 5000, 200))
