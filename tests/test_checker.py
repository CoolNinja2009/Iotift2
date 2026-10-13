"""Checker: every rule that turns a mistake into a compile error."""

import pytest

from helpers import errors, error_text


def ok(src):
    errs = errors(src)
    assert errs == [], errs


# ── types and units ────────────────────────────────────────────────────

def test_literals_adapt_to_context():
    ok('var a: u8 = 200\nvar b: f32 = 2\nvar c: i64 = 1 << 40\nconst N = 8\nvar d: u8 = N\n')


@pytest.mark.parametrize('src,msg', [
    ('var a: u8 = 300\n', '300 does not fit in `u8`'),
    ('var a: i32 = 1.5\n', 'found a decimal number'),
    ('var a: i32 = 1\nvar b: u32 = 2\nfn f() -> i32 => a + b\n', 'cannot mix `i32` and `u32`'),
    ('var a: f32 = 1.0\nvar b: i32 = 2\nfn f() -> f32 => a * b\n', 'cannot mix'),
    ('var t: bool = 1\n', 'expected `bool`, found `{integer}`'),
    ('var x: int = 0\n', 'did you mean `i32`?'),
    ('fn f() -> i32 => 7 % 2.0\n', '`%` needs integers'),
])
def test_type_errors(src, msg):
    assert msg in error_text(src)


def test_widening_is_implicit():
    ok('fn f(a: u8, b: i16, c: f32) -> i32 do\n  let x: i32 = a\n  let y: f64 = c\n  return x + b\nend\n')


def test_durations_need_units():
    assert '`wait` needs a time with a unit' in error_text('task t do\n  wait 500\nend\n')
    assert 'timer period needs a unit' in error_text('every 500 => pass\n')
    assert 'cannot mix `Duration` and `{integer}`' in \
        error_text('fn f() -> Duration => 5ms + 3\n')
    ok('fn f(n: i32) -> Duration => 5ms * n + 1s\n')


def test_duration_casts_point_to_methods():
    assert '.as_ms()' in error_text('fn f(d: Duration) -> i64 => d as i64\n')
    ok('fn f(d: Duration) -> i64 => d.as_ms()\n')


def test_immutability():
    assert '`x` is immutable' in error_text('fn f() do\n  let x = 1\n  x = 2\nend\n')
    assert 'declare the parameter as `mut x`' in error_text('fn f(x: i32) do\n  x = 2\nend\n')
    assert '`L` is a constant' in error_text('const L = 1\nfn f() do\n  L = 2\nend\n')
    assert 'declare the method with `mut self`' in error_text(
        'struct S do\n  a: i32\nend\nimpl S do\n  fn f(self) do\n    self.a = 1\n  end\nend\n')


def test_missing_return():
    assert 'must return a `i32` on every path' in error_text(
        'fn f(x: i32) -> i32 do\n  if x > 0 => return 1\nend\n')
    ok('fn f(x: i32) -> i32 do\n  if x > 0 do\n    return 1\n  else\n    return 2\n  end\nend\n')


def test_match_must_be_exhaustive():
    src = 'enum M do\n  a, b, c\nend\nfn f(m: M) do\n  match m do\n    M.a => pass\n    b => pass\n  end\nend\n'
    assert 'does not handle `M.c`' in error_text(src)
    assert 'needs a `_ => ...` arm' in error_text('fn f(x: i32) do\n  match x do\n    1 => pass\n  end\nend\n')


def test_struct_construction():
    src = 'struct P do\n  x: f32\n  y: f32 = 0.0\nend\n'
    ok(src + 'fn f() -> P => P(x: 1.0)\n')
    assert 'missing field `x`' in error_text(src + 'fn f() -> P => P(y: 1.0)\n')
    assert '`P` has no field `z`' in error_text(src + 'fn f() -> P => P(x: 1.0, z: 2.0)\n')


def test_unknown_names_get_hints():
    assert 'use `wait 100ms` inside a `task`' in error_text('fn f() do\n  delay(100)\nend\n')
    assert 'add `use c.math`' in error_text('fn f() -> f32 => atan2(1.0, 2.0)\n')
    assert 'declare a `pin`' in error_text('fn f() do\n  digitalWrite(2, 1)\nend\n')


def test_array_bounds_known_at_compile_time():
    assert 'index 4 is out of range' in error_text('var a: [i32; 4]\nfn f() -> i32 => a[4]\n')


# ── non-blocking rules ─────────────────────────────────────────────────

def test_wait_only_in_tasks():
    msg = '`wait` can only be used inside a `task`'
    assert msg in error_text('fn f() do\n  wait 1s\nend\n')
    assert msg in error_text('every 1s do\n  wait 10ms\nend\n')
    ok('task t do\n  wait 1s\nend\n')


def test_task_loop_must_wait():
    assert 'never waits' in error_text('task t do\n  loop do\n    pass\n  end\nend\n')


def test_handler_loop_must_end():
    assert 'never ends' in error_text('every 1s do\n  loop do\n    pass\n  end\nend\n')
    ok('every 1s do\n  loop do\n    break\n  end\nend\n')


# ── cores ──────────────────────────────────────────────────────────────

def test_globals_belong_to_one_core():
    src = '''
core 1 as control do
    var integral: f32 = 0.0
end
core 0 as network do
    every 1s => integral = 0.0
end
'''
    assert '`integral` belongs to core 1 (control), but this timer runs on core 0 (network)' \
        in error_text(src)


def test_ownership_follows_function_calls():
    src = '''
var count: i32 = 0
fn bump() do
    count += 1
end
fn outer() do
    bump()
end
task t on core 0 do
    loop do
        outer()
        wait 1s
    end
end
'''
    assert '`outer` uses `count` (through `bump`), which belongs to core 1' in error_text(src)


def test_shared_values_cross_cores():
    src = '''
shared var hits: u32 = 0
every 1s => hits.add(1)
task t on core 0 do
    loop do
        println("{hits.get()}")
        wait 1s
    end
end
'''
    ok(src)
    assert 'read it with `hits.get()`' in error_text('shared var hits: u32 = 0\nfn f() -> u32 => hits\n')
    assert 'use `hits.set(value)`' in error_text('shared var hits: u32 = 0\nfn f() do\n  hits = 1\nend\n')
    assert 'holds bool, f32 or an integer' in error_text('shared var x: f64 = 0.0\n')


# ── hardware ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('src,msg', [
    ('pin a = out 34\n', 'GPIO 34 is input-only'),
    ('pin a = out 6\n', 'wired to the flash chip'),
    ('pin a = out 2\npin b = in 2\n', 'GPIO 2 is already used by pin `a`'),
    ('pin a = adc 5\n', 'GPIO 5 has no ADC'),
    ('pin a = out 50\n', 'GPIO 50 does not exist'),
    ('device esp32c3\npin a = out 25\n', 'does not exist on the esp32c3'),
    ('pin a = pwm 13, freq 1MHz, bits 10\n', 'the LEDC runs at 80 MHz'),
    ('pin a = in 4, pull sideways\n', '`pull` must be one of'),
    ('pin a = out 2, debounce 5ms\n', '`out` pins have no option `debounce`'),
    ('device esp9000\n', 'unknown chip'),
])
def test_pin_rules(src, msg):
    assert msg in error_text(src)


def test_pins_use_methods():
    assert 'pins are changed with methods' in error_text('pin led = out 2\nfn f() do\n  led = 1\nend\n')
    assert "`out` pins have no method `read`" in error_text('pin led = out 2\nfn f() -> u16 => led.read()\n')
    assert 'events need an `in` pin' in error_text('pin led = out 2\non led.press => pass\n')
