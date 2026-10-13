"""Iotift 2 parser: tokens -> AST (nodes.py).

Every block is `do ... end`; a single statement can follow `=>` instead.
Parse errors are collected (the parser resynchronises at the next line) and
raised together as one CompileError. Any parse error is fatal.
"""

from . import nodes as N
from .errors import Diagnostic, CompileError
from .lexer import Lexer, Interp

ASSIGN_OPS = {'=', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>='}
COMPARE_OPS = {'==', '!=', '<', '<=', '>', '>='}
PIN_KINDS = {'out', 'in', 'adc', 'pwm'}
PIN_EVENTS = {'press', 'release', 'rising', 'falling', 'change'}
SYSTEM_EVENTS = {'boot', 'ready'}
BLOCK_END = ('end', 'elif', 'else')


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens, file='<input>'):
        self.toks = tokens
        self.pos = 0
        self.file = file
        self.errors = []
        self.no_cast = False

    # ── token helpers ──────────────────────────────────────────────────

    @property
    def tok(self):
        return self.toks[self.pos]

    def peek(self, k=1):
        j = min(self.pos + k, len(self.toks) - 1)
        return self.toks[j]

    def at(self, kind, value=None):
        return self.tok.is_(kind, value)

    def at_kw(self, *words):
        return self.tok.kind == 'kw' and self.tok.value in words

    def at_word(self, word):
        """A contextual keyword: an identifier that acts as a keyword here."""
        return self.tok.kind == 'ident' and self.tok.value == word

    def at_op(self, *ops):
        return self.tok.kind == 'op' and self.tok.value in ops

    def next(self):
        t = self.tok
        if t.kind != 'eof':
            self.pos += 1
        return t

    def fail(self, msg, tok=None, help=None):
        tok = tok or self.tok
        raise ParseError(Diagnostic(msg, tok.line, tok.col, help, self.file))

    def describe(self, t):
        if t.kind == 'nl':
            return 'end of line'
        if t.kind == 'eof':
            return 'end of file'
        if t.kind == 'str':
            return 'a string'
        return f'`{t.value}`'

    def expect_op(self, op, help=None):
        if not self.at_op(op):
            self.fail(f'expected `{op}`, found {self.describe(self.tok)}', help=help)
        return self.next()

    def expect_kw(self, kw, help=None):
        if not self.at_kw(kw):
            self.fail(f'expected `{kw}`, found {self.describe(self.tok)}', help=help)
        return self.next()

    def ident(self, what='a name'):
        if self.tok.kind != 'ident':
            extra = ' (it is a keyword)' if self.tok.kind == 'kw' else ''
            self.fail(f'expected {what}, found {self.describe(self.tok)}{extra}')
        return self.next().value

    def skip_nl(self):
        while self.at('nl'):
            self.next()

    def end_stmt(self):
        """A statement ends at a newline or right before `end`/`elif`/`else`."""
        if self.at('nl'):
            self.skip_nl()
        elif self.at('eof') or self.at_kw(*BLOCK_END):
            pass
        else:
            self.fail(f'expected end of line, found {self.describe(self.tok)}',
                      help='put each statement on its own line')

    def sync(self):
        while not self.at('nl') and not self.at('eof'):
            self.next()
        self.skip_nl()

    def node(self, cls, tok, *args):
        n = cls(*args)
        n.line, n.col = tok.line, tok.col
        return n

    # ── program ────────────────────────────────────────────────────────

    def parse(self):
        items = []
        self.skip_nl()
        while not self.at('eof'):
            try:
                items.append(self.item(top=True))
            except ParseError as e:
                self.errors.append(e.args[0])
                self.sync()
            self.skip_nl()
        if self.errors:
            raise CompileError(self.errors)
        prog = N.Program(items, self.file)
        return prog

    def item(self, top):
        pub = False
        if self.at_kw('pub'):
            self.next()
            pub = True
        node = self.item_inner(top, pub)
        node.pub = pub
        return node

    def item_inner(self, top, pub):
        t = self.tok
        if self.at_kw('extern'):
            return self.extern_block(pub)
        if self.at_kw('device'):
            self.next()
            chip = self.ident('a chip name such as esp32 or esp32s3')
            self.end_stmt()
            return self.node(N.DeviceDecl, t, chip)
        if self.at_kw('use'):
            return self.use_decl()
        if self.at_kw('const', 'let', 'var', 'shared'):
            return self.global_decl(pub)
        if self.at_kw('fn'):
            return self.fn_decl(pub)
        if self.at_kw('struct'):
            return self.struct_decl(pub)
        if self.at_kw('enum'):
            return self.enum_decl(pub)
        if self.at_kw('impl'):
            return self.impl_decl()
        if self.at_kw('pin'):
            return self.pin_decl()
        if self.at('ident', 'wifi') and self.peek().is_('ident') and self.peek(2).is_('kw', 'do'):
            return self.wifi_decl(pub)
        if self.at_kw('every'):
            return self.every_decl()
        if self.at_kw('after'):
            self.next()
            delay = self.expr()
            body = self.body()
            self.end_stmt()
            return self.node(N.After, t, delay, body)
        if self.at_kw('on'):
            return self.on_decl()
        if self.at_kw('task'):
            return self.task_decl()
        if self.at_kw('core'):
            if not top:
                self.fail('`core` blocks cannot be nested')
            return self.core_block()
        if self.at('ident') and self.tok.value in ('class', 'void', 'int', 'float', 'def', 'function'):
            self.fail(f'`{self.tok.value}` is not Iotift',
                      help='functions are `fn name(x: i32) -> i32 do ... end`')
        self.fail(f'expected a declaration, found {self.describe(self.tok)}',
                  help='statements go inside `fn`, `task`, `every`, `after` or `on` blocks')

    def use_decl(self):
        t = self.next()
        path = [self.ident('a module path')]
        while self.at_op('.'):
            self.next()
            path.append(self.ident('a module name'))
        alias = None
        names = None
        if self.at_op('('):
            self.next()
            names = []
            while not self.at_op(')'):
                names.append(self.ident('a name to import'))
                if not self.at_op(')'):
                    self.expect_op(',')
            self.next()
        if self.at_kw('as'):
            self.next()
            alias = self.ident()
        self.end_stmt()
        return self.node(N.UseDecl, t, path, alias, names)

    def global_decl(self, pub):
        t = self.tok
        kind = self.next().value
        if kind == 'shared':
            self.expect_kw('var', help='write `shared var name: type = value`')
        name = self.ident()
        ty = None
        if self.at_op(':'):
            self.next()
            ty = self.type_ref()
        init = None
        if self.at_op('='):
            self.next()
            init = self.expr()
        self.end_stmt()
        return self.node(N.GlobalDecl, t, kind, name, ty, init, pub)

    def type_ref(self):
        t = self.tok
        if self.at_op('['):
            self.next()
            elem = self.type_ref()
            if not self.at_op(';'):
                self.fail('expected `;` in array type', help='arrays are written `[i32; 8]`')
            self.next()
            size = self.expr()
            self.expect_op(']')
            return self.node(N.ArrayTypeRef, t, elem, size)
        name = self.ident('a type')
        while self.at_op('.'):           # a type from another module: ntc.Reading
            self.next()
            name += '.' + self.ident('a type')
        return self.node(N.TypeName, t, name)

    # ── functions and types ────────────────────────────────────────────

    def fn_decl(self, pub, owner=None):
        t = self.next()
        name = self.ident('a function name')
        self.expect_op('(')
        params = []
        self_kind = None
        while not self.at_op(')'):
            pt = self.tok
            mutable = False
            if self.at_kw('mut'):
                self.next()
                mutable = True
            if self.at_kw('self'):
                if owner is None:
                    self.fail('`self` is only allowed in methods inside `impl`')
                if params or self_kind:
                    self.fail('`self` must be the first parameter')
                self.next()
                self_kind = 'mut self' if mutable else 'self'
            else:
                # Binding parameter names only document the C++ signature, so
                # words like `pin` or `in` are fine here.
                if self.at('kw') and self.peek(1).is_('op', ':'):
                    pname = self.next().value
                else:
                    pname = self.ident('a parameter name')
                self.expect_op(':', help=f'give the parameter a type: `{pname}: i32`')
                pty = self.type_ref()
                default = None
                if self.at_op('='):
                    self.next()
                    default = self.expr()
                params.append(self.node(N.Param, pt, pname, pty, mutable, default))
            if not self.at_op(')'):
                self.expect_op(',')
        self.next()
        ret = None
        if self.at_op('->'):
            self.next()
            ret = self.type_ref()
        if self.at_op('=>'):
            et = self.next()
            e = self.expr()
            body = [self.node(N.Return, et, e) if ret is not None else self.node(N.ExprStmt, et, e)]
        else:
            body = self.block()
        self.end_stmt()
        return self.node(N.FnDecl, t, name, params, ret, body, pub, self_kind, owner)

    def struct_decl(self, pub):
        t = self.next()
        name = self.ident('a struct name')
        self.expect_kw('do', help='struct fields go in a `do ... end` block')
        self.skip_nl()
        fields = []
        while not self.at_kw('end'):
            ft = self.tok
            fname = self.ident('a field name')
            self.expect_op(':', help=f'give the field a type: `{fname}: f32`')
            fty = self.type_ref()
            default = None
            if self.at_op('='):
                self.next()
                default = self.expr()
            fields.append(self.node(N.Field, ft, fname, fty, default))
            if self.at_op(','):
                self.next()
            self.skip_nl()
        self.next()
        self.end_stmt()
        return self.node(N.StructDecl, t, name, fields, pub)

    def enum_decl(self, pub):
        t = self.next()
        name = self.ident('an enum name')
        self.expect_kw('do', help='enum variants go in a `do ... end` block')
        self.skip_nl()
        variants = []
        while not self.at_kw('end'):
            variants.append((self.ident('a variant name'), self.toks[self.pos - 1]))
            if self.at_op(','):
                self.next()
            self.skip_nl()
        self.next()
        self.end_stmt()
        e = self.node(N.EnumDecl, t, name, [v for v, _ in variants], pub)
        e.variant_pos = {v: (tk.line, tk.col) for v, tk in variants}
        return e

    def impl_decl(self):
        t = self.next()
        tname = self.ident('a type name')
        self.expect_kw('do')
        self.skip_nl()
        fns = []
        while not self.at_kw('end'):
            pub = False
            if self.at_kw('pub'):
                self.next()
                pub = True
            if not self.at_kw('fn'):
                self.fail('only `fn` declarations are allowed inside `impl`')
            fns.append(self.fn_decl(pub, owner=tname))
            self.skip_nl()
        self.next()
        self.end_stmt()
        return self.node(N.ImplDecl, t, tname, fns)

    # ── C++ bindings ───────────────────────────────────────────────────

    def string_value(self, what):
        if not self.at('str') or any(not isinstance(p, str) for p in self.tok.value):
            self.fail(f'expected {what} as a plain string')
        return ''.join(self.next().value)

    def extern_block(self, pub):
        t = self.next()
        if not self.at_word('cpp'):
            self.fail('expected `cpp` after `extern`', help='write `extern cpp "Header.h" do ... end`')
        self.next()
        header = self.string_value('a header file name')
        lib = None
        if self.at_word('from'):
            self.next()
            if not self.at_word('lib'):
                self.fail('expected `lib` after `from`',
                          help='write `from lib "owner/Library@^1.2.3"`')
            self.next()
            lib = self.string_value('a PlatformIO library')
        start = self.expect_kw('do')
        self.skip_nl()
        items = []
        while not self.at_kw('end'):
            if self.at('eof'):
                self.fail('`extern` block is missing its `end`', start)
            try:
                items.append(self.extern_item(in_class=False))
            except ParseError as e:
                self.errors.append(e.args[0])
                self.sync()
            self.skip_nl()
        self.next()
        self.end_stmt()
        return self.node(N.ExternBlock, t, header, lib, items, pub)

    def alias(self, cpp):
        if self.at_kw('as'):
            self.next()
            return self.ident('an Iotift name')
        return cpp

    def extern_item(self, in_class):
        t = self.tok
        if self.at_kw('const') and not in_class:
            self.next()
            cpp = self.ident('a C++ constant')
            self.expect_op(':', help='give the constant a type: `const NEO_GRB: u32`')
            ty = self.type_ref()
            name = self.alias(cpp)
            self.end_stmt()
            return self.node(N.ExternConst, t, name, ty, cpp)
        if self.at_kw('let', 'var') and not in_class:
            mutable = self.next().value == 'var'
            cpp = self.ident('a C++ object')
            self.expect_op(':', help='give the object a type: `var Wire: TwoWire`')
            ty = self.type_ref()
            name = self.alias(cpp)
            self.end_stmt()
            return self.node(N.ExternObject, t, name, ty, cpp, mutable)
        if self.at_kw('fn'):
            return self.extern_fn(in_class)
        if in_class and self.at_word('new'):
            self.next()
            params = self.extern_params(False)[0]
            self.end_stmt()
            fn = self.node(N.ExternFn, t, 'new', params, None, '')
            return fn
        if self.at_word('class') and not in_class:
            self.next()
            cpp = self.ident('a C++ class name')
            name = self.alias(cpp)
            start = self.expect_kw('do')
            self.skip_nl()
            ctor = None
            methods = []
            while not self.at_kw('end'):
                if self.at('eof'):
                    self.fail('`class` is missing its `end`', start)
                try:
                    m = self.extern_item(in_class=True)
                    if m.name == 'new' and m.cpp == '':
                        if ctor is not None:
                            self.fail('a class binding has one `new`', m)
                        ctor = m.params
                    else:
                        methods.append(m)
                except ParseError as e:
                    self.errors.append(e.args[0])
                    self.sync()
                self.skip_nl()
            self.next()
            self.end_stmt()
            return self.node(N.ExternClass, t, name, cpp, ctor, methods)
        what = '`fn` or `new`' if in_class else '`const`, `let`, `var`, `fn` or `class`'
        self.fail(f'expected {what} in a binding, found {self.describe(self.tok)}')

    def extern_params(self, in_class):
        self.expect_op('(')
        params = []
        self_kind = None
        while not self.at_op(')'):
            pt = self.tok
            mutable = False
            if self.at_kw('mut'):
                self.next()
                mutable = True
            if self.at_kw('self'):
                if not in_class or params or self_kind:
                    self.fail('`self` must be the first parameter of a class method')
                self.next()
                self_kind = 'mut self' if mutable else 'self'
            else:
                # Binding parameter names only document the C++ signature, so
                # words like `pin` or `in` are fine here.
                if self.at('kw') and self.peek(1).is_('op', ':'):
                    pname = self.next().value
                else:
                    pname = self.ident('a parameter name')
                self.expect_op(':', help=f'give the parameter a type: `{pname}: i32`')
                pty = self.type_ref()
                default = None
                if self.at_op('='):
                    self.next()
                    default = self.expr()
                params.append(self.node(N.Param, pt, pname, pty, mutable, default))
            if not self.at_op(')'):
                self.expect_op(',')
        self.next()
        return params, self_kind

    def extern_fn(self, in_class):
        t = self.next()
        cpp = self.ident('a C++ function name')
        params, self_kind = self.extern_params(in_class)
        if in_class and self_kind is None:
            self.fail('class methods take `self` or `mut self` first', t)
        ret = None
        if self.at_op('->'):
            self.next()
            ret = self.type_ref()
        blocking, max_block = False, None
        if self.at_word('blocking'):
            self.next()
            blocking = True
            if self.at_op('('):
                self.next()
                if not self.at_word('max'):
                    self.fail('expected `max:`', help='write `blocking(max: 2ms)`')
                self.next()
                self.expect_op(':')
                max_block = self.expr()
                self.expect_op(')')
        name = self.alias(cpp)
        self.end_stmt()
        return self.node(N.ExternFn, t, name, params, ret, cpp, blocking, max_block, self_kind)

    # ── hardware and events ────────────────────────────────────────────

    def pin_decl(self):
        t = self.next()
        name = self.ident('a pin name')
        self.expect_op('=', help='write `pin led = out 2`')
        kt = self.tok
        if self.at_kw('in'):
            kind = 'in'
            self.next()
        elif self.at('ident') and self.tok.value in PIN_KINDS:
            kind = self.next().value
        else:
            hint = None
            if self.at('ident') and self.tok.value in ('output', 'input', 'analog'):
                hint = {'output': 'out', 'input': 'in', 'analog': 'adc'}[self.tok.value]
                hint = f'write `{hint}`'
            self.fail(f'expected a pin kind (out, in, adc, pwm), found {self.describe(self.tok)}', kt,
                      help=hint)
        number = self.unary()
        options = {}
        while self.at_op(','):
            self.next()
            ot = self.tok
            oname = self.ident('a pin option')
            if oname in options:
                self.fail(f'option `{oname}` given twice', ot)
            if self.at('ident') or self.at_kw('on'):
                vt = self.next()
                value = ('word', vt.value)
            else:
                value = ('expr', self.unary())
            options[oname] = (value, ot.line, ot.col)
        self.end_stmt()
        return self.node(N.PinDecl, t, name, kind, number, options)

    def every_decl(self):
        t = self.next()
        self.no_cast = True   # `every 1s as name`: `as` names the timer here
        try:
            period = self.expr()
        finally:
            self.no_cast = False
        offset = None
        label = None
        if self.at_word('offset'):
            self.next()
            self.no_cast = True
            try:
                offset = self.expr()
            finally:
                self.no_cast = False
        if self.at_kw('as'):
            self.next()
            label = self.ident('a timer name')
        body = self.body()
        self.end_stmt()
        return self.node(N.Every, t, period, offset, label, body)

    def on_decl(self):
        t = self.next()
        if self.at('ident') and self.tok.value in SYSTEM_EVENTS and \
                (self.peek().is_('kw', 'do') or self.peek().is_('op', '=>')):
            ev = self.next().value
            body = self.body()
            self.end_stmt()
            return self.node(N.OnEvent, t, None, ev, body)
        st = self.tok
        src = self.node(N.Name, st, self.ident('a pin name or `boot` / `ready`'))
        self.expect_op('.', help='write `on button.press do ... end`')
        et = self.tok
        ev = self.ident('an event name')
        body = self.body()
        self.end_stmt()
        node = self.node(N.OnEvent, t, src, ev, body)
        node.event_tok = (et.line, et.col)
        return node

    def wifi_decl(self, pub):
        t = self.next()
        name = self.ident('a WiFi name')
        start = self.expect_kw('do')
        self.skip_nl()
        options = {}
        while not self.at_kw('end'):
            if self.at('eof'):
                self.fail('`wifi` block is missing its `end`', start)
            try:
                ot = self.tok
                if self.at('kw'):
                    key = self.next().value
                else:
                    key = self.ident('a WiFi setting such as `ssid`')
                self.expect_op(':', help=f'write `{key}: value`')
                value = self.expr()
                if key in options:
                    self.fail(f'`{key}` is set twice', ot)
                options[key] = (value, ot.line, ot.col)
                self.end_stmt()
            except ParseError as e:
                self.errors.append(e.args[0])
                self.sync()
            self.skip_nl()
        self.next()
        self.end_stmt()
        return self.node(N.WifiDecl, t, name, options, pub)

    def task_decl(self):
        t = self.next()
        name = self.ident('a task name')
        core = None
        if self.at_kw('on'):
            self.next()
            self.expect_kw('core')
            core = self.core_number()
        body = self.body()
        self.end_stmt()
        task = self.node(N.TaskDecl, t, name, body)
        task.core = core
        return task

    def core_number(self):
        ct = self.tok
        if not self.at('int') or self.tok.unit:
            self.fail('expected a core number (0 or 1)')
        n = self.next().value
        if n not in (0, 1):
            self.fail(f'the ESP32 has cores 0 and 1, not {n}', ct)
        return n

    def core_block(self):
        t = self.next()
        n = self.core_number()
        label = None
        if self.at_kw('as'):
            self.next()
            label = self.ident()
        self.expect_kw('do')
        self.skip_nl()
        items = []
        while not self.at_kw('end'):
            if self.at('eof'):
                self.fail('`core` block is missing its `end`', t)
            try:
                items.append(self.item(top=False))
            except ParseError as e:
                self.errors.append(e.args[0])
                self.sync()
            self.skip_nl()
        self.next()
        self.end_stmt()
        return self.node(N.CoreBlock, t, n, label, items)

    # ── blocks and statements ──────────────────────────────────────────

    def body(self):
        """`do ... end` or `=> statement`."""
        if self.at_op('=>'):
            self.next()
            return [self.statement(single=True)]
        return self.block()

    def block(self):
        if not self.at_kw('do'):
            self.fail(f'expected `do` or `=>` to start the block, found {self.describe(self.tok)}',
                      help='blocks are `do ... end`; a single statement can follow `=>`')
        start = self.next()
        stmts = self.stmts_until(('end',), start)
        self.next()
        return stmts

    def stmts_until(self, enders, start):
        stmts = []
        self.skip_nl()
        while not self.at_kw(*enders):
            if self.at('eof'):
                self.fail('this block is missing its `end`', start)
            if self.at_kw('elif', 'else', 'end') and not self.at_kw(*enders):
                self.fail(f'unexpected `{self.tok.value}`')
            try:
                stmts.append(self.statement())
            except ParseError as e:
                self.errors.append(e.args[0])
                self.sync()
            self.skip_nl()
        return stmts

    def statement(self, single=False):
        t = self.tok
        s = self.statement_inner()
        if not single:
            self.end_stmt()
        return s

    def statement_inner(self):
        t = self.tok
        if self.at_kw('let', 'var'):
            mutable = self.next().value == 'var'
            name = self.ident()
            ty = None
            if self.at_op(':'):
                self.next()
                ty = self.type_ref()
            init = None
            if self.at_op('='):
                self.next()
                init = self.expr()
            return self.node(N.VarDecl, t, mutable, name, ty, init)
        if self.at_kw('if'):
            return self.if_stmt()
        if self.at_kw('while'):
            self.next()
            cond = self.expr()
            return self.node(N.While, t, cond, self.body())
        if self.at_kw('for'):
            self.next()
            var = self.ident('a loop variable')
            self.expect_kw('in')
            it = self.expr()
            step = None
            if self.at_word('by'):
                self.next()
                step = self.expr()
            return self.node(N.For, t, var, it, step, self.body())
        if self.at_kw('loop'):
            self.next()
            return self.node(N.Loop, t, self.body())
        if self.at_kw('match'):
            return self.match_stmt()
        if self.at_kw('break'):
            self.next()
            return self.node(N.Break, t)
        if self.at_kw('continue'):
            self.next()
            return self.node(N.Continue, t)
        if self.at_kw('pass'):
            self.next()
            return self.node(N.Pass, t)
        if self.at_kw('return'):
            self.next()
            value = None
            if not (self.at('nl') or self.at('eof') or self.at_kw(*BLOCK_END)):
                value = self.expr()
            return self.node(N.Return, t, value)
        if self.at_kw('wait'):
            self.next()
            if self.at_word('until'):
                self.next()
                cond = self.expr()
                timeout = None
                if self.at_word('timeout'):
                    self.next()
                    timeout = self.expr()
                return self.node(N.Wait, t, 'until', cond, timeout)
            return self.node(N.Wait, t, 'for', self.expr())
        if self.at_kw('fn', 'struct', 'enum', 'pin', 'every', 'task', 'impl', 'core'):
            self.fail(f'`{self.tok.value}` declarations must be at the top level of the file')
        e = self.expr()
        if self.at_op(*ASSIGN_OPS):
            op = self.next().value
            value = self.expr()
            return self.node(N.Assign, t, e, op, value)
        return self.node(N.ExprStmt, t, e)

    def if_stmt(self):
        t = self.next()
        cond = self.expr()
        if self.at_op('=>'):
            self.next()
            return self.node(N.If, t, [(cond, [self.statement(single=True)])], None)
        start = self.expect_kw('do', help='write `if condition do ... end`')
        branches = [(cond, self.stmts_until(('elif', 'else', 'end'), start))]
        else_body = None
        while True:
            if self.at_kw('elif'):
                self.next()
                c = self.expr()
                st = self.expect_kw('do')
                branches.append((c, self.stmts_until(('elif', 'else', 'end'), st)))
            elif self.at_kw('else'):
                st = self.next()
                if self.at_kw('if'):
                    self.fail('write `elif` instead of `else if`')
                else_body = self.stmts_until(('end',), st)
            else:
                break
        self.expect_kw('end')
        return self.node(N.If, t, branches, else_body)

    def match_stmt(self):
        t = self.next()
        subject = self.expr()
        self.expect_kw('do')
        self.skip_nl()
        arms = []
        while not self.at_kw('end'):
            if self.at('eof'):
                self.fail('`match` is missing its `end`', t)
            at = self.tok
            pats = [self.pattern()]
            while self.at_op('|'):
                self.next()
                pats.append(self.pattern())
            guard = None
            if self.at_kw('if'):
                self.next()
                guard = self.expr()
            if self.at_op('=>'):
                self.next()
                body = [self.statement(single=True)]
            elif self.at_kw('do'):
                body = self.block()
            else:
                self.fail('expected `=>` or `do` after the pattern')
            self.end_stmt()
            arms.append(self.node(N.MatchArm, at, pats, guard, body))
        self.next()
        return self.node(N.Match, t, subject, arms)

    def pattern(self):
        t = self.tok
        if self.at('ident') and self.tok.value == '_':
            self.next()
            return self.node(N.WildcardPat, t)
        lo = self.unary()
        if self.at_op('..', '..='):
            inclusive = self.next().value == '..='
            hi = self.unary()
            return self.node(N.RangeExpr, t, lo, hi, inclusive)
        return lo

    # ── expressions ────────────────────────────────────────────────────

    def expr(self):
        if self.at_kw('if'):
            t = self.next()
            cond = self.or_expr()
            self.expect_kw('then', help='an `if` expression is `if c then a else b`')
            a = self.expr()
            self.expect_kw('else', help='an `if` expression needs an `else`')
            b = self.expr()
            return self.node(N.IfExpr, t, cond, a, b)
        return self.or_expr()

    def or_expr(self):
        e = self.and_expr()
        while self.at_kw('or'):
            t = self.next()
            e = self.node(N.Binary, t, 'or', e, self.and_expr())
        return e

    def and_expr(self):
        e = self.not_expr()
        while self.at_kw('and'):
            t = self.next()
            e = self.node(N.Binary, t, 'and', e, self.not_expr())
        return e

    def not_expr(self):
        if self.at_kw('not'):
            t = self.next()
            return self.node(N.Unary, t, 'not', self.not_expr())
        if self.at_op('!') and not self.peek().is_('op', '='):
            self.fail('use `not` instead of `!`')
        return self.compare()

    def compare(self):
        e = self.range_expr()
        if self.at_op(*COMPARE_OPS):
            t = self.next()
            e = self.node(N.Binary, t, t.value, e, self.range_expr())
            if self.at_op(*COMPARE_OPS):
                self.fail('comparisons cannot be chained', help='write `a < b and b < c`')
        return e

    def range_expr(self):
        e = self.bitor()
        if self.at_op('..', '..='):
            t = self.next()
            e = self.node(N.RangeExpr, t, e, self.bitor(), t.value == '..=')
        return e

    def binary_level(self, sub, ops):
        e = sub()
        while self.at_op(*ops):
            t = self.next()
            e = self.node(N.Binary, t, t.value, e, sub())
        return e

    def bitor(self):
        return self.binary_level(self.bitxor, ('|',))

    def bitxor(self):
        return self.binary_level(self.bitand, ('^',))

    def bitand(self):
        return self.binary_level(self.shift, ('&',))

    def shift(self):
        return self.binary_level(self.additive, ('<<', '>>'))

    def additive(self):
        return self.binary_level(self.multiplicative, ('+', '-'))

    def multiplicative(self):
        return self.binary_level(self.cast, ('*', '/', '%'))

    def cast(self):
        e = self.unary()
        while self.at_kw('as') and not self.no_cast:
            t = self.next()
            e = self.node(N.Cast, t, e, self.type_ref())
        return e

    def unary(self):
        if self.at_op('-', '~'):
            t = self.next()
            return self.node(N.Unary, t, t.value, self.unary())
        if self.at_op('&') or self.at_op('*'):
            self.fail('Iotift has no pointers', help='pass values directly; use `mut` parameters to modify them')
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.at_op('('):
                t = self.next()
                args = []
                self.skip_nl()
                while not self.at_op(')'):
                    at = self.tok
                    name = None
                    if self.at('ident') and self.peek().is_('op', ':'):
                        name = self.next().value
                        self.next()
                    args.append(self.node(N.Arg, at, name, self.expr()))
                    if not self.at_op(')'):
                        self.expect_op(',')
                self.next()
                c = self.node(N.Call, t, e, args)
                c.line, c.col = e.line, e.col
                e = c
            elif self.at_op('.'):
                t = self.next()
                if self.tok.kind == 'kw' and self.tok.value in ('on',):
                    name = self.next().value
                else:
                    name = self.ident('a field or method name')
                m = self.node(N.Member, t, e, name)
                e = m
            elif self.at_op('['):
                t = self.next()
                idx = self.expr()
                self.expect_op(']')
                e = self.node(N.Index, t, e, idx)
            else:
                return e

    def primary(self):
        t = self.tok
        if t.kind == 'int':
            self.next()
            return self.node(N.IntLit, t, t.value, t.unit)
        if t.kind == 'float':
            self.next()
            return self.node(N.FloatLit, t, t.value, t.unit)
        if t.kind == 'str':
            self.next()
            parts = []
            for p in t.value:
                if isinstance(p, Interp):
                    parts.append(self.node(N.Interpolation, t, self.sub_expr(p), p.fmt))
                else:
                    parts.append(p)
            return self.node(N.StrLit, t, parts)
        if t.kind == 'kw' and t.value in ('true', 'false'):
            self.next()
            return self.node(N.BoolLit, t, t.value == 'true')
        if t.kind == 'kw' and t.value == 'self':
            self.next()
            return self.node(N.SelfRef, t)
        if t.kind == 'ident':
            self.next()
            return self.node(N.Name, t, t.value)
        if self.at_op('('):
            self.next()
            e = self.expr()
            self.expect_op(')')
            return e
        if self.at_op('['):
            self.next()
            items = []
            while not self.at_op(']'):
                items.append(self.expr())
                if not self.at_op(']'):
                    self.expect_op(',')
            self.next()
            return self.node(N.ArrayLit, t, items)
        if t.kind == 'kw' and t.value == 'wait':
            self.fail('`wait` is a statement', help='put it on its own line')
        self.fail(f'expected an expression, found {self.describe(t)}')

    def sub_expr(self, interp):
        lx = Lexer(interp.src, self.file)
        lx.line, lx.col = interp.line, interp.col
        toks = [tk for tk in lx.tokenize() if tk.kind != 'nl']
        sub = Parser(toks, self.file)
        e = sub.expr()
        if not sub.at('eof'):
            sub.fail(f'unexpected {sub.describe(sub.tok)} in `{{...}}`')
        return e


def parse(source, file='<input>'):
    lx = Lexer(source, file)
    return Parser(lx.tokenize(), file).parse()
