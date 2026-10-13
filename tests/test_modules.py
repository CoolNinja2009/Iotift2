"""Multi-file programs (`use`) and C++ bindings (`extern cpp`)."""

from helpers import errors, error_text, run, texts, compile_source, CompileError

STRIP = '''
pub extern cpp "FakeStrip.h" from lib "fake/FakeStrip@^1.0" do
    const FAKE_STRIP_MAX: u16 as STRIP_MAX
    fn fake_rgb(r: u8, g: u8, b: u8) -> u32 as rgb
    fn fake_reset_bus() blocking
    class FakeStrip as Strip do
        new(count: u16, pin: u8)
        fn begin(mut self)
        fn setPixel(mut self, i: u16, c: u32) as set
        fn getPixel(self, i: u16) -> u32 as get
        fn show(mut self)
        fn count(self) -> u16
        fn slowFlush(mut self) blocking(max: 50ms) as flush
        fn getPixel(self, i: u16) -> u32 as show
    end
end
'''

# The same binding without the duplicate-method line, for programs that run.
STRIP_OK = STRIP.replace('        fn getPixel(self, i: u16) -> u32 as show\n', '')

GEOM = '''
pub struct Point do
    x: i32 = 0
    y: i32 = 0
end

pub enum Dir do
    north
    south
end

impl Point do
    pub fn moved(self, d: Dir) -> Point do
        match d do
            Dir.north => return Point(x: self.x, y: self.y + 1)
            Dir.south => return Point(x: self.x, y: self.y - 1)
        end
    end
end

pub fn add(a: Point, b: Point) -> Point => Point(x: a.x + b.x, y: a.y + b.y)
fn secret() -> i32 => 42
pub const ORIGIN_X: i32 = 7
'''


# ── modules ──────────────────────────────────────────────────────────

def test_qualified_names_from_a_module():
    src = '''
use util.geom

on boot do
    let p = geom.add(geom.Point(x: 1, y: 2), geom.Point(x: geom.ORIGIN_X))
    let q = p.moved(geom.Dir.north)
    println("{p.x},{p.y} {q.y}")
end
'''
    assert texts(run(src, 5, files={'util/geom.iot': GEOM})) == ['8,2 3']


def test_selective_import_and_alias():
    src = '''
use util.geom (Point, add) as g

on boot do
    let p = add(Point(x: 1), Point(y: 5))
    println("{p.x} {p.y} {g.ORIGIN_X}")
end
'''
    assert texts(run(src, 5, files={'util/geom.iot': GEOM})) == ['1 5 7']


def test_private_names_stay_private():
    src = '''
use util.geom (secret)

on boot do
    println("{geom.secret()}")
end
'''
    text = error_text(src, files={'util/geom.iot': GEOM})
    assert '`secret` is private to module `util.geom`' in text
    assert 'mark it `pub` in util/geom.iot' in text


def test_same_name_in_two_modules_does_not_clash():
    a = 'pub fn value() -> i32 => 1\nvar count: i32 = 10\npub fn bump() do\n    count += 1\nend\n' \
        'pub fn get() -> i32 => count\n'
    b = 'pub fn value() -> i32 => 2\nvar count: i32 = 20\npub fn get() -> i32 => count\n'
    src = '''
use lib.a
use lib.b

fn value() -> i32 => 3

on boot do
    a.bump()
    println("{a.value()} {b.value()} {value()} {a.get()} {b.get()}")
end
'''
    assert texts(run(src, 5, files={'lib/a.iot': a, 'lib/b.iot': b})) == ['1 2 3 11 20']


def test_modules_can_use_modules():
    files = {
        'util/geom.iot': GEOM,
        'shapes/box.iot': 'use util.geom (Point)\n\npub struct Box do\n    corner: Point\n'
                          '    size: i32 = 1\nend\n\npub fn area(b: Box) -> i32 => b.size * b.size\n',
    }
    src = '''
use shapes.box
use util.geom

on boot do
    let b = box.Box(corner: geom.Point(x: 3), size: 4)
    println("{box.area(b)} {b.corner.x}")
end
'''
    assert texts(run(src, 5, files=files)) == ['16 3']


def test_missing_module():
    text = error_text('use drivers.nothing\n', files={})
    assert 'cannot find module `drivers.nothing`' in text
    assert 'drivers/nothing.iot' in text


def test_modules_that_use_each_other_are_rejected():
    files = {'a.iot': 'use b\npub fn f() -> i32 => 1\n', 'b.iot': 'use a\npub fn g() -> i32 => 2\n'}
    assert any('use each other' in m for m in errors('use a\n', files=files))


def test_unknown_member_of_module():
    msgs = errors('use util.geom\n\non boot do\n    let p = geom.nope()\nend\n',
                  files={'util/geom.iot': GEOM})
    assert 'module `util.geom` has no `nope`' in msgs


def test_errors_name_the_module_file():
    try:
        compile_source('use bad\n', 'main.iot', files={'bad.iot': 'pub fn f() -> i32 => true\n'})
    except CompileError as e:
        assert e.diagnostics[0].file == 'bad.iot'
        assert 'pub fn f()' in e.render()
    else:
        raise AssertionError('expected an error')


def test_device_only_in_the_entry_file():
    msgs = errors('use m\n', files={'m.iot': 'device esp32\n'})
    assert msgs and 'device' in msgs[0]


# ── extern cpp ───────────────────────────────────────────────────────

def test_class_binding_methods_consts_and_functions():
    src = STRIP_OK + '''
var leds = Strip(4, 5)

on boot do
    leds.begin()
end

every 100ms do
    for i in 0..leds.count() do
        leds.set(i, rgb(1, 2, 3))
    end
    leds.show()
    println("{leds.get(3):x} {STRIP_MAX}")
end
'''
    out = texts(run(src, 250))
    assert out == ['strip begin 4', 'show 1', '10203 64', 'show 2', '10203 64']


def test_library_from_a_module_is_passed_by_reference():
    src = '''
use drivers.strip

var leds = strip.Strip(3, 5)

fn paint(mut s: strip.Strip, c: u32) do
    for i in 0..s.count() do
        s.set(i, c)
    end
    s.show()
end

on boot do
    paint(leds, 0xABCDEF)
    println("{leds.get(2):x}")
end
'''
    assert texts(run(src, 5, files={'drivers/strip.iot': STRIP_OK})) == ['show 1', 'abcdef']


def test_lib_dep_and_header_are_recorded():
    from helpers import check_source
    info = check_source(STRIP_OK + 'var leds = Strip(4, 5)\n', files={})
    assert info.includes == ['FakeStrip.h']
    assert info.lib_deps == ['fake/FakeStrip@^1.0']


def test_duplicate_method_in_binding():
    assert '`Strip` already has a method `show`' in errors(STRIP)


def test_blocking_call_rejected_in_a_timer():
    src = STRIP_OK + '''
every 1s do
    fake_reset_bus()
end
'''
    text = error_text(src)
    assert '`fake_reset_bus` can block for an unknown time, which would stall core 1' in text
    assert 'on boot' in text


def test_blocking_call_through_a_function_is_found():
    src = STRIP_OK + '''
fn reset() do
    fake_reset_bus()
end

every 1s do
    reset()
end
'''
    assert any('calls `fake_reset_bus`' in m for m in errors(src))


def test_blocking_call_allowed_in_boot():
    src = STRIP_OK + '''
fn reset() do
    fake_reset_bus()
end

on boot do
    reset()
    println("ok")
end
'''
    assert texts(run(src, 5)) == ['ok']


def test_blocking_with_a_long_maximum_is_rejected_short_one_allowed():
    src = STRIP_OK + '''
var leds = Strip(4, 5)

every 1s do
    leds.flush()
end
'''
    assert any('can block for up to 50ms' in m for m in errors(src))
    short = src.replace('blocking(max: 50ms)', 'blocking(max: 2ms)')
    assert errors(short) == []


def test_library_objects_cannot_be_copied_or_made_locally():
    src = STRIP_OK + '''
var a = Strip(4, 5)

on boot do
    let b = Strip(2, 3)
end
'''
    assert any('created at the top level' in m for m in errors(src))
    src = STRIP_OK + 'var a = Strip(4, 5)\nvar b = a\n'
    assert any('cannot be copied' in m for m in errors(src))


def test_library_objects_are_not_struct_fields():
    src = STRIP_OK + 'struct Holder do\n    s: Strip\nend\n'
    assert 'a struct cannot hold a library object' in errors(src)


def test_binding_parameter_types_are_checked():
    src = 'extern cpp "x.h" do\n    fn f(a: [u8; 4])\nend\n'
    assert any('bindings take numbers' in m for m in errors(src))


def test_extern_needs_cpp():
    assert any('expected `cpp`' in m for m in errors('extern c "x.h" do\nend\n'))


def test_spec_neopixel_binding_with_default_argument():
    src = '''
extern cpp "Adafruit_NeoPixel.h" from lib "adafruit/Adafruit NeoPixel@^1.12.3" do
    class Adafruit_NeoPixel as NeoPixel do
        new(count: u16, pin: i16, kind: u32 = NEO_GRB + NEO_KHZ800)
        fn begin(mut self)
        fn setPixelColor(mut self, i: u16, rgb: u32) as set
        fn show(mut self) blocking(max: 2ms)
        fn numPixels(self) -> u16 as len
    end
    const NEO_GRB: u32
    const NEO_KHZ800: u32
end

var strip = NeoPixel(3, 48)

every 10ms do
    strip.set(1, 0xFF00)
    strip.show()
end
'''
    lines = run(src, 15)
    assert '[10] NEOPIXEL 000000 00ff00 000000' in lines
