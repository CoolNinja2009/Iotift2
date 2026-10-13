"""Lexer and parser: do/end blocks, newlines, units, strings, errors."""

import pytest

from helpers import errors, error_text
from iotift2.lexer import tokenize
from iotift2.parser import parse
from iotift2.errors import CompileError
from iotift2 import nodes as N


def kinds(src):
    return [(t.kind, t.value) for t in tokenize(src) if t.kind != 'eof']


def test_units_and_suffixes():
    toks = [t for t in tokenize('500ms 2s 1.5s 25kHz 4kb 7u8 0xFF 0b1010 1_000') if t.kind != 'nl'][:-1]
    assert [(t.value, t.unit) for t in toks] == [
        (500, 'ms'), (2, 's'), (1.5, 's'), (25, 'kHz'), (4, 'kb'), (7, 'u8'), (255, None),
        (10, None), (1000, None)]


def test_unknown_suffix_is_an_error():
    with pytest.raises(CompileError) as e:
        tokenize('let x = 5sec')
    assert 'unknown number suffix `sec`' in str(e.value)


def test_comments():
    src = '-- line\n--- doc\n{- block {- nested -} -}\nlet x = 1 -- trailing\n'
    assert kinds(src) == [('kw', 'let'), ('ident', 'x'), ('op', '='), ('int', 1), ('nl', '\n')]


def test_semicolon_and_braces_are_rejected_with_help():
    with pytest.raises(CompileError) as e:
        tokenize('let x = 1;')
    assert "doesn't use semicolons" in str(e.value)
    with pytest.raises(CompileError) as e:
        tokenize('if x { }')
    assert '`do ... end`' in str(e.value)


def test_newline_continues_after_operator_and_inside_parens():
    src = 'let x = 1 +\n    2\nlet y = f(1,\n  2)\n'
    assert [v for k, v in kinds(src) if k == 'nl'] == ['\n', '\n']


def test_string_interpolation_and_braces():
    t = tokenize('"a {x + 1:.2} {{b}} {f("q")}"')[0]
    assert t.value[0] == 'a '
    assert (t.value[1].src, t.value[1].fmt) == ('x + 1', '.2')
    assert t.value[2] == ' {b} '
    assert t.value[3].src == 'f("q")'


def test_blocks_elif_else_and_one_liners():
    prog = parse('''
fn sign(x: i32) -> i32 do
    if x > 0 do
        return 1
    elif x < 0 do
        return -1
    else
        return 0
    end
end
fn sq(x: f32) -> f32 => x * x
every 1s => println("tick")
''')
    fn = prog.items[0]
    assert isinstance(fn.body[0], N.If)
    assert len(fn.body[0].branches) == 2 and fn.body[0].else_body
    assert isinstance(prog.items[1].body[0], N.Return)
    assert isinstance(prog.items[2], N.Every)


def test_one_line_do_end():
    prog = parse('every 1s do println("a") end\n')
    assert len(prog.items[0].body) == 1


def test_bitwise_binds_tighter_than_comparison():
    prog = parse('fn f(a: u8) -> bool => a & 4 == 0\n')
    e = prog.items[0].body[0].value
    assert e.op == '==' and e.left.op == '&'


def test_every_as_names_the_timer():
    prog = parse('every 500ms as blink => pass\n')
    assert prog.items[0].label == 'blink'


@pytest.mark.parametrize('src,msg', [
    ('fn f() do\n  pass\n', 'missing its `end`'),
    ('fn f() do\n if x do\n pass\n else if y do\n pass\n end\nend\n', 'write `elif` instead of `else if`'),
    ('int x = 5\n', '`int` is not Iotift'),
    ('x = 5\n', 'expected a declaration'),
    ('fn f() do\n  x = 1 y = 2\nend\n', 'expected end of line'),
    ('fn f() do\n  if a < b < c => pass\nend\n', 'cannot be chained'),
    ('pin led = output 2\n', 'write `out`'),
])
def test_parse_errors(src, msg):
    assert msg in error_text(src)


def test_several_parse_errors_are_reported_together():
    errs = errors('fn f() do\n  x = = 1\n  y = = 2\nend\n')
    assert len(errs) == 2
