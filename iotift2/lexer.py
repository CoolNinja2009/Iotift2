"""Iotift 2 lexer: source text -> tokens.

Blocks are `do ... end`, statements end at a newline. A NEWLINE token is
emitted only where it can end a statement: not inside ( ) or [ ], and not
after a token that needs a right-hand side (a binary operator, a comma, ...).
"""

from .errors import Diagnostic, CompileError

# `by`, `offset`, `timeout` and `until` are contextual: keywords only where
# they can appear (`for ... by`, `every ... offset`, `wait until ... timeout`).
KEYWORDS = {
    'and', 'as', 'after', 'break', 'const', 'continue', 'core', 'device', 'do',
    'elif', 'else', 'end', 'enum', 'every', 'extern', 'false', 'fn', 'for', 'if', 'impl', 'in',
    'let', 'loop', 'match', 'mut', 'not', 'on', 'or', 'pass', 'pin', 'pub',
    'return', 'self', 'shared', 'struct', 'task', 'then', 'true',
    'use', 'var', 'wait', 'while',
}

# Longest first.
OPERATORS = [
    '<<=', '>>=', '..=',
    '..', '=>', '->', '==', '!=', '<=', '>=', '<<', '>>', '+=', '-=', '*=', '/=', '%=',
    '&=', '|=', '^=',
    '+', '-', '*', '/', '%', '&', '|', '^', '~', '<', '>', '=', '(', ')', '[', ']',
    ',', '.', ':', '?', '!',
]

# A newline right after one of these does not end the statement.
CONTINUES = {
    '+', '-', '*', '/', '%', '&', '|', '^', '~', '<', '>', '=', '==', '!=', '<=', '>=',
    '<<', '>>', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>=', ',', '.',
    '..', '..=', '->', ':', 'and', 'or', 'not', 'as',
}

DURATION_UNITS = {'us': 1, 'ms': 1000, 's': 1_000_000, 'min': 60_000_000, 'h': 3_600_000_000}
FREQ_UNITS = {'Hz': 1, 'kHz': 1000, 'MHz': 1_000_000}
SIZE_UNITS = {'b': 1, 'kb': 1024, 'mb': 1024 * 1024}
OTHER_UNITS = {'db', 'baud'}
TYPE_SUFFIXES = {'i8', 'i16', 'i32', 'i64', 'u8', 'u16', 'u32', 'u64', 'usize', 'f32', 'f64'}


class Token:
    __slots__ = ('kind', 'value', 'line', 'col', 'unit')

    def __init__(self, kind, value, line, col, unit=None):
        self.kind = kind      # int float str ident kw op nl eof
        self.value = value
        self.line = line
        self.col = col
        self.unit = unit      # numbers: 'ms', 'kHz', 'u8', ... or None

    def is_(self, kind, value=None):
        return self.kind == kind and (value is None or self.value == value)

    def __repr__(self):
        u = f' {self.unit}' if self.unit else ''
        return f'Token({self.kind} {self.value!r}{u} @{self.line}:{self.col})'


class Interp:
    """An `{expr:fmt}` hole inside a string literal."""

    def __init__(self, src, fmt, line, col):
        self.src = src
        self.fmt = fmt
        self.line = line
        self.col = col

    def __repr__(self):
        return f'Interp({self.src!r}, {self.fmt!r})'


class Lexer:
    def __init__(self, source, file='<input>'):
        self.src = source
        self.file = file
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens = []
        self.depth = 0
        self.errors = []

    def error(self, msg, line=None, col=None, help=None):
        self.errors.append(Diagnostic(msg, line or self.line, col or self.col, help, self.file))

    def peek(self, k=0):
        j = self.i + k
        return self.src[j] if j < len(self.src) else ''

    def advance(self, n=1):
        for _ in range(n):
            if self.i >= len(self.src):
                return
            if self.src[self.i] == '\n':
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.i += 1

    def add(self, kind, value, line, col, unit=None):
        self.tokens.append(Token(kind, value, line, col, unit))

    def newline(self, line, col):
        if self.depth > 0 or not self.tokens:
            return
        last = self.tokens[-1]
        if last.kind == 'nl':
            return
        if last.kind in ('op', 'kw') and last.value in CONTINUES:
            return
        self.add('nl', '\n', line, col)

    def tokenize(self):
        while self.i < len(self.src):
            c = self.peek()
            line, col = self.line, self.col
            if c == '\n':
                self.newline(line, col)
                self.advance()
            elif c in ' \r':
                self.advance()
            elif c == '\t':
                self.advance()  # tabs are harmless with do/end blocks
            elif c == '\\' and self.peek(1) == '\n':
                self.advance(2)  # explicit line continuation
            elif c == '-' and self.peek(1) == '-':
                while self.i < len(self.src) and self.peek() != '\n':
                    self.advance()
            elif c == '{' and self.peek(1) == '-':
                self.block_comment()
            elif c == ';' and self.depth > 0:
                self.add('op', ';', line, col)  # only used in array types: [u8; 16]
                self.advance()
            elif c == ';':
                self.error("Iotift doesn't use semicolons", help='end the statement with a new line')
                self.advance()
            elif c.isdigit():
                self.number()
            elif c.isalpha() or c == '_':
                self.word()
            elif c == '"':
                self.string()
            elif c == 'r' and self.peek(1) == '"':
                self.string()
            elif c == "'":
                self.char()
            else:
                for op in OPERATORS:
                    if self.src.startswith(op, self.i):
                        if op in ('(', '['):
                            self.depth += 1
                        elif op in (')', ']'):
                            self.depth = max(0, self.depth - 1)
                        self.add('op', op, line, col)
                        self.advance(len(op))
                        break
                else:
                    hint = None
                    if c in '{}':
                        hint = 'blocks are written `do ... end`, not with braces'
                    elif c == '#':
                        hint = 'comments start with `--`'
                    self.error(f'unexpected character {c!r}', help=hint)
                    self.advance()
        self.newline(self.line, self.col)
        self.add('eof', None, self.line, self.col)
        if self.errors:
            raise CompileError(self.errors)
        return self.tokens

    def block_comment(self):
        line, col = self.line, self.col
        depth = 0
        while self.i < len(self.src):
            if self.peek() == '{' and self.peek(1) == '-':
                depth += 1
                self.advance(2)
            elif self.peek() == '-' and self.peek(1) == '}':
                depth -= 1
                self.advance(2)
                if depth == 0:
                    return
            else:
                self.advance()
        self.error('unterminated block comment', line, col, help='close it with `-}`')

    def word(self):
        line, col = self.line, self.col
        start = self.i
        while self.peek().isalnum() or self.peek() == '_':
            self.advance()
        w = self.src[start:self.i]
        if w in ('r',) and self.peek() == '"':
            self.i, self.line, self.col = start, line, col
            self.string()
            return
        self.add('kw' if w in KEYWORDS else 'ident', w, line, col)

    def number(self):
        line, col = self.line, self.col
        start = self.i
        is_float = False
        if self.peek() == '0' and self.peek(1) in 'xXbBoO' and self.peek(1):
            base = {'x': 16, 'b': 2, 'o': 8}[self.peek(1).lower()]
            self.advance(2)
            digits = '0123456789abcdefABCDEF_' if base == 16 else ('01_' if base == 2 else '01234567_')
            s = self.i
            while self.peek() and self.peek() in digits:
                self.advance()
            text = self.src[s:self.i].replace('_', '')
            if not text:
                self.error('missing digits after number prefix', line, col)
                text = '0'
            value = int(text, base)
        else:
            while self.peek().isdigit() or self.peek() == '_':
                self.advance()
            if self.peek() == '.' and self.peek(1).isdigit():
                is_float = True
                self.advance()
                while self.peek().isdigit() or self.peek() == '_':
                    self.advance()
            if self.peek() in ('e', 'E') and (self.peek(1).isdigit() or
                                              (self.peek(1) in '+-' and self.peek(2).isdigit())):
                is_float = True
                self.advance(2)
                while self.peek().isdigit():
                    self.advance()
            text = self.src[start:self.i].replace('_', '')
            value = float(text) if is_float else int(text)
        unit = None
        if self.peek().isalpha():
            s = self.i
            while self.peek().isalnum():
                self.advance()
            unit = self.src[s:self.i]
            known = set(DURATION_UNITS) | set(FREQ_UNITS) | set(SIZE_UNITS) | OTHER_UNITS | TYPE_SUFFIXES
            if unit not in known:
                self.error(f'unknown number suffix `{unit}`', line, col,
                           help='time: us ms s min h; frequency: Hz kHz MHz; size: b kb mb; '
                                'type: i8..i64 u8..u64 f32 f64')
        self.add('float' if is_float else 'int', value, line, col, unit)

    def char(self):
        line, col = self.line, self.col
        self.advance()
        c = self.peek()
        if c == '\\':
            self.advance()
            c = {'n': '\n', 't': '\t', '0': '\0', '\\': '\\', "'": "'"}.get(self.peek(), self.peek())
        self.advance()
        if self.peek() != "'":
            self.error('character literal must hold exactly one character', line, col,
                       help='use double quotes for strings')
        else:
            self.advance()
        self.add('int', ord(c) if c else 0, line, col, 'u8')

    def string(self):
        line, col = self.line, self.col
        raw = self.peek() == 'r'
        if raw:
            self.advance()
        self.advance()  # opening quote
        parts = []
        buf = []
        while True:
            c = self.peek()
            if c == '' or c == '\n':
                self.error('unterminated string', line, col, help='close it with `"` on the same line')
                break
            if c == '"':
                self.advance()
                break
            if raw:
                buf.append(c)
                self.advance()
                continue
            if c == '\\':
                self.advance()
                e = self.peek()
                mapping = {'n': '\n', 't': '\t', 'r': '\r', '0': '\0', '\\': '\\', '"': '"',
                           '{': '{', '}': '}'}
                if e not in mapping:
                    self.error(f'unknown escape `\\{e}`')
                buf.append(mapping.get(e, e))
                self.advance()
            elif c == '{' and self.peek(1) == '{':
                buf.append('{')
                self.advance(2)
            elif c == '}' and self.peek(1) == '}':
                buf.append('}')
                self.advance(2)
            elif c == '{':
                if buf:
                    parts.append(''.join(buf))
                    buf = []
                parts.append(self.interpolation())
            elif c == '}':
                self.error('single `}` in string', help='write `}}` for a literal brace')
                self.advance()
            else:
                buf.append(c)
                self.advance()
        if buf or not parts:
            parts.append(''.join(buf))
        self.add('str', parts, line, col)

    def interpolation(self):
        line, col = self.line, self.col
        self.advance()  # {
        start = self.i
        depth = 0
        fmt_at = None
        while True:
            c = self.peek()
            if c == '' or c == '\n':
                self.error('unterminated `{` in string', line, col)
                return Interp('0', None, line, col + 1)
            if c == '"':
                # nested string literal inside the expression
                self.advance()
                while self.peek() not in ('"', '', '\n'):
                    if self.peek() == '\\':
                        self.advance()
                    self.advance()
                self.advance()
                continue
            if c in '([':
                depth += 1
            elif c in ')]':
                depth -= 1
            elif c == ':' and depth == 0 and fmt_at is None:
                fmt_at = self.i
            elif c == '}' and depth == 0:
                break
            self.advance()
        end = self.i
        self.advance()  # }
        if fmt_at is not None:
            src, fmt = self.src[start:fmt_at], self.src[fmt_at + 1:end].strip()
        else:
            src, fmt = self.src[start:end], None
        if not src.strip():
            self.error('empty `{}` in string', line, col, help='write `{{}}` for literal braces')
            src = '0'
        return Interp(src, fmt, line, col + 1)


def tokenize(source, file='<input>'):
    return Lexer(source, file).tokenize()
