"""Iotift 2 checker: names, types, units, mutability, waits and core ownership.

It annotates the AST in place (expression types, resolved names and call
kinds) for the code generator, and collects every error before raising.
"""

import os
import re

from . import nodes as N
from . import typesys as T
from .chips import CHIPS
from .errors import Diagnostic, CompileError
from .lexer import DURATION_UNITS, FREQ_UNITS, SIZE_UNITS

ERR = T.SpecialTy('{error}')
DEFAULT_CORE = 1
BLOCK_BUDGET_US = 10_000      # a handler or task step may block for at most this long

MATH_1 = {'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh', 'exp', 'log',
          'log10', 'log2', 'sqrt', 'cbrt', 'floor', 'ceil', 'round', 'trunc', 'deg', 'rad'}
MATH_2 = {'atan2', 'pow', 'hypot', 'fmod', 'fmin', 'fmax', 'copysign'}
MATH_N = {'lerp': 3, 'map_range': 5}
MATH_CONSTS = {'pi', 'e', 'tau'}
PRELUDE_FLOAT_1 = {'sqrt', 'sin', 'cos', 'tan', 'floor', 'ceil', 'round', 'exp', 'log'}

PIN_METHODS = {
    'out': {'on': ([], T.VOID), 'off': ([], T.VOID), 'toggle': ([], T.VOID),
            'set': ([T.BOOL], T.VOID), 'is_on': ([], T.BOOL)},
    'in': {'level': ([], T.BOOL), 'is_high': ([], T.BOOL), 'is_low': ([], T.BOOL),
           'is_pressed': ([], T.BOOL)},
    'adc': {'read': ([], T.U16), 'read_mv': ([], T.U32)},
    'pwm': {'write': ([T.U32], T.VOID), 'duty': ([T.F32], T.VOID), 'max': ([], T.U32)},
}
PIN_OPTIONS = {
    'out': {'active': ('word', {'low', 'high'}), 'initial': ('word', {'on', 'off'})},
    'in': {'pull': ('word', {'up', 'down', 'none'}), 'debounce': ('duration', None)},
    'adc': {'atten': ('db', {0, 2.5, 6, 11})},
    'pwm': {'freq': ('freq', None), 'bits': ('int', None)},
}

PIN_EVENTS = {'press', 'release', 'rising', 'falling', 'change'}

# WiFi: one station and one access point at most (the radio has one of each).
WIFI_STATES = ['idle', 'connecting', 'connected', 'retrying', 'failed']
WIFI_OPTIONS = {
    'sta': ('mode', 'ssid', 'password', 'hostname', 'retry', 'timeout', 'power_save', 'autoconnect'),
    'ap': ('mode', 'ssid', 'password', 'channel', 'max_clients'),
}
WIFI_EVENTS = {'sta': ('connected', 'lost', 'failed'), 'ap': ('client_joined', 'client_left')}
WIFI_METHODS = {'sta': ('connect', 'disconnect'), 'ap': ()}
WIFI_PROPS = {'sta': ('connected', 'state', 'ip', 'rssi', 'mac'), 'ap': ('clients', 'ip', 'mac')}
# The original ESP32 cannot read ADC2 while the radio is on.
ESP32_ADC2 = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27}


class Sym:
    def __init__(self, kind, name, ty, decl=None, mutable=False, core=None):
        self.kind = kind          # var let const shared param local fn type pin timer task module
        self.name = name
        self.ty = ty
        self.decl = decl
        self.mutable = mutable
        self.core = core          # owning core for globals and timers
        self.const_value = None
        self.task = None          # TaskDecl when this local lives in a task frame
        self.uid = None
        self.cname = None         # C++ name for top-level symbols
        self.pub = False

    def __repr__(self):
        return f'Sym({self.kind} {self.name}: {self.ty})'


ERROR_SYM = Sym('error', '{error}', ERR)


class Ctx:
    def __init__(self, kind, core=None, ret=T.VOID, fn=None, task=None, self_ty=None,
                 self_mut=False, label=''):
        self.kind = kind          # fn task handler global
        self.core = core
        self.ret = ret
        self.fn = fn
        self.task = task
        self.self_ty = self_ty
        self.self_mut = self_mut
        self.loops = 0
        self.touches = []         # (global sym, node)
        self.calls = []           # (fn sym, node)
        self.blocking = []        # (ExternFn, node) calls that may block
        self.boot = False         # `on boot` / `on ready`: blocking calls are fine here
        self.label = label


class Info:
    """What the code generator needs beyond the annotated AST."""

    def __init__(self):
        self.chip = CHIPS['esp32']
        self.structs = []         # StructTy in dependency order
        self.enums = []
        self.globals = []         # GlobalDecl
        self.fns = []             # FnDecl (free functions)
        self.methods = []         # FnDecl with owner
        self.pins = []            # PinDecl (with .gpio, .opts)
        self.timers = []          # Every
        self.afters = []
        self.events = []          # OnEvent (pin events)
        self.system = {'boot': [], 'ready': []}
        self.tasks = []
        self.cores_used = set()
        self.uses_math = False
        self.uses_serial = False
        self.includes = []        # extern cpp headers
        self.lib_deps = []        # PlatformIO libraries
        self.wifi = []            # WifiDecl
        self.wifi_events = []     # OnEvent on a wifi


class Module:
    """One .iot file. The entry module's names are emitted unchanged; every
    other module's top-level names get a `<path>__` prefix in C++."""

    def __init__(self, name, prog, source=None, entry=False):
        self.name = name
        self.prog = prog
        self.source = source
        self.entry = entry
        self.prefix = '' if entry else name.replace('.', '_') + '__'
        self.scope = {}


class Checker:
    def __init__(self, prog, source=None, root=None):
        if isinstance(prog, N.Program):
            self.modules = [Module('main', prog, source, entry=True)]
        else:
            self.modules = list(prog)
        self.by_name = {m.name: m for m in self.modules}
        self.mod = self.modules[-1]
        self.prog = self.mod.prog
        self.file = self.prog.file
        self.source = source
        self.errors = []
        self.scopes = []
        self.info = Info()
        self.ctx = None
        self.fn_ctx = {}          # fn sym -> Ctx
        self.contexts = []        # handler / task contexts
        self.core_labels = {}
        self.root = root          # folder of the entry file: `.env` for `secret(...)`
        self.wifi_state = None    # the built-in WifiState enum, made on first use

    # ── diagnostics ────────────────────────────────────────────────────

    def err(self, msg, node, help=None):
        d = Diagnostic(msg, getattr(node, 'line', 0), getattr(node, 'col', 0), help, self.file)
        key = (d.message, d.help, d.line, d.col, d.file)
        if not any((e.message, e.help, e.line, e.col, e.file) == key for e in self.errors):
            self.errors.append(d)
        return ERR

    # ── scopes ─────────────────────────────────────────────────────────

    def push(self):
        self.scopes.append({})

    def pop(self):
        self.scopes.pop()

    def declare(self, sym, node):
        scope = self.scopes[-1]
        if sym.name in scope:
            prev = scope[sym.name].decl
            where = f' (first declared on line {prev.line})' if prev is not None and prev.line else ''
            self.err(f'`{sym.name}` is already declared{where}', node)
            return sym
        if len(self.scopes) == 1 and sym.kind == 'type' and not isinstance(sym.ty, T.ClassTy):
            sym.ty.cname = self.mod.prefix + sym.name + '_'
        if len(self.scopes) == 1 and getattr(sym, 'cname', None) is None and \
                sym.kind in ('var', 'let', 'const', 'shared', 'pin', 'fn'):
            sym.cname = self.mod.prefix + sym.name + '_'
            if sym.decl is not None and sym.kind in ('pin', 'fn'):
                sym.decl.cname = sym.cname
        sym.pub = bool(getattr(sym.decl, 'pub', False))
        if len(self.scopes) > 1 and sym.name in self.scopes[0] and \
                self.scopes[0][sym.name].kind in ('fn', 'type', 'pin', 'module'):
            self.err(f'`{sym.name}` hides the {self.scopes[0][sym.name].kind} of the same name', node,
                     help='pick a different name')
        scope[sym.name] = sym
        return sym

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        if name == 'WifiState':
            return self.wifi_state_sym()
        return None

    def wifi_state_sym(self):
        if self.wifi_state is None:
            ty = T.EnumTy('WifiState', N.EnumDecl('WifiState', WIFI_STATES))
            ty.cname = 'iot_WifiState'
            self.info.enums.insert(0, ty)
            self.wifi_state = Sym('type', 'WifiState', ty)
        return self.wifi_state

    # ── entry point ────────────────────────────────────────────────────

    def check(self):
        for mod in self.modules:
            self.check_module(mod)
        self.check_ownership()
        self.check_wifi_adc()
        if self.errors:
            order = {m.prog.file: i for i, m in enumerate(self.modules)}
            self.errors.sort(key=lambda d: (order.get(d.file, 0), d.line, d.col))
            raise CompileError(self.errors)
        return self.info

    def check_module(self, mod):
        self.mod = mod
        self.prog = mod.prog
        self.file = mod.prog.file
        self.scopes = [mod.scope]
        items = []
        for it in self.prog.items:
            if isinstance(it, N.CoreBlock):
                if it.label:
                    self.core_labels[it.core] = it.label
                for sub in it.items:
                    if isinstance(sub, (N.DeviceDecl, N.UseDecl, N.StructDecl, N.EnumDecl,
                                        N.ImplDecl, N.FnDecl, N.PinDecl)):
                        self.err('only variables, tasks, timers and event handlers can go in a '
                                 '`core` block', sub,
                                 help='declare functions, types and pins at the top level')
                        continue
                    if isinstance(sub, N.TaskDecl) and sub.core is not None and sub.core != it.core:
                        self.err(f'task `{sub.name}` is inside `core {it.core}` but asks for '
                                 f'core {sub.core}', sub)
                    sub.core = it.core
                    items.append(sub)
            else:
                items.append(it)
        for it in items:
            it.prefix = mod.prefix
            it.file = mod.prog.file
            if isinstance(it, N.ImplDecl):
                for fn in it.fns:
                    fn.file = mod.prog.file
            if isinstance(it, N.DeviceDecl) and not mod.entry:
                self.err('`device` belongs in the main file', it)
        self.items = items
        self.collect(items)
        for it in items:
            self.check_item(it)

    # ── pass 1: declarations ───────────────────────────────────────────

    def collect(self, items):
        info = self.info
        devices = [it for it in items if isinstance(it, N.DeviceDecl)]
        if len(devices) > 1:
            self.err('only one `device` line is allowed', devices[1])
        if devices:
            chip = CHIPS.get(devices[0].chip)
            if chip is None:
                self.err(f'unknown chip `{devices[0].chip}`', devices[0],
                         help='supported: ' + ', '.join(CHIPS))
            else:
                info.chip = chip
        # types first, so everything else can refer to them
        for it in items:
            if isinstance(it, N.ExternBlock):
                if it.header not in info.includes:
                    info.includes.append(it.header)
                if it.lib and it.lib not in info.lib_deps:
                    info.lib_deps.append(it.lib)
                for x in it.items:
                    x.pub = it.pub
                    if isinstance(x, N.ExternClass):
                        cty = T.ClassTy(x.name, x.cpp, x)
                        x.sym = self.declare(Sym('type', x.name, cty, x), x)
        for it in items:
            if isinstance(it, N.StructDecl):
                self.declare(Sym('type', it.name, T.StructTy(it.name, it), it), it)
            elif isinstance(it, N.EnumDecl):
                ty = T.EnumTy(it.name, it)
                if len(set(it.variants)) != len(it.variants):
                    self.err(f'enum `{it.name}` lists a variant twice', it)
                if not it.variants:
                    self.err(f'enum `{it.name}` has no variants', it)
                self.declare(Sym('type', it.name, ty, it), it)
                info.enums.append(ty)
            elif isinstance(it, N.UseDecl):
                self.use(it)
        for it in items:
            if isinstance(it, N.StructDecl):
                sty = self.lookup(it.name).ty
                for f in it.fields:
                    if f.name in sty.fields:
                        self.err(f'field `{f.name}` is declared twice', f)
                    fty = self.resolve_type(f.type)
                    if isinstance(fty, T.ClassTy):
                        self.err('a struct cannot hold a library object', f,
                                 help='keep the object at the top level and pass it to functions')
                    sty.fields[f.name] = (fty, f)
        info.structs.extend(st for st in self.order_structs(items) if st not in info.structs)
        for it in items:
            if isinstance(it, N.ImplDecl):
                s = self.lookup(it.type_name)
                if s is None or not isinstance(s.ty, T.StructTy):
                    self.err(f'`impl` needs a struct; `{it.type_name}` is not one', it)
                    continue
                for fn in it.fns:
                    if fn.name in s.ty.methods or fn.name in s.ty.fields:
                        self.err(f'`{it.type_name}` already has a member called `{fn.name}`', fn)
                        continue
                    s.ty.methods[fn.name] = fn
                    fn.owner = s.ty
                    self.fn_signature(fn)
                    info.methods.append(fn)
        for it in items:
            if isinstance(it, N.ExternBlock):
                self.extern_block(it)
                self.binding_defaults(it)
        for it in items:
            if isinstance(it, N.FnDecl):
                self.fn_signature(it)
                it.sym = self.declare(Sym('fn', it.name, None, it), it)
                info.fns.append(it)
            elif isinstance(it, N.PinDecl):
                self.pin(it)
            elif isinstance(it, N.WifiDecl):
                self.wifi(it)
            elif isinstance(it, N.GlobalDecl):
                pass  # declared in order during pass 2 so initialisers see earlier ones only
            elif isinstance(it, N.Every) and it.label:
                it.sym = self.declare(Sym('timer', it.label, T.TIMER, it,
                                          core=it.core if it.core is not None else DEFAULT_CORE), it)
            elif isinstance(it, N.TaskDecl):
                it.sym = self.declare(Sym('task', it.name, T.TASK, it), it)
        # globals: declare all now (functions may use globals declared below them)
        for it in items:
            if isinstance(it, N.GlobalDecl):
                self.global_decl(it)

    def extern_block(self, block):
        for x in block.items:
            if isinstance(x, N.ExternClass):
                cty = x.sym.ty
                if x.ctor is not None:
                    for p in x.ctor:
                        p.ty = self.resolve_type(p.type)
                        self.binding_type(p.ty, p)
                    cty.ctor = x.ctor
                for m in x.methods:
                    if m.name in cty.methods:
                        self.err(f'`{x.name}` already has a method `{m.name}`', m)
                        continue
                    self.extern_signature(m)
                    m.owner = cty
                    cty.methods[m.name] = m
            elif isinstance(x, N.ExternFn):
                self.extern_signature(x)
                x.sym = self.declare(Sym('fn', x.name, None, x), x)
                x.sym.cname = x.cname = x.cpp
            elif isinstance(x, N.ExternConst):
                ty = self.resolve_type(x.type)
                if ty is not ERR and not (T.is_num(ty) or ty in (T.BOOL, T.STR)):
                    self.err('binding constants must be numbers, `bool` or `str`', x)
                sym = Sym('const', x.name, ty, x)
                sym.cname = x.cpp
                x.sym = self.declare(sym, x)
            elif isinstance(x, N.ExternObject):
                ty = self.resolve_type(x.type)
                sym = Sym('var' if x.mutable else 'let', x.name, ty, x, mutable=x.mutable,
                          core=DEFAULT_CORE)
                sym.cname = x.cpp
                x.sym = self.declare(sym, x)

    def binding_defaults(self, block):
        """Default arguments may name the block's constants, so they are checked last."""
        params = []
        for x in block.items:
            if isinstance(x, N.ExternClass):
                params += x.ctor or []
                for m in x.methods:
                    params += m.params
            elif isinstance(x, N.ExternFn):
                params += x.params
        for p in params:
            if p.default is not None and p.ty is not None:
                t = self.expr_safe(p.default, p.ty)
                self.coerce(p.default, t, p.ty)

    def extern_signature(self, fn):
        fn.ret_ty = self.resolve_type(fn.ret) if fn.ret is not None else T.VOID
        if isinstance(fn.ret_ty, T.ClassTy):
            self.err('a binding cannot return a library object by value', fn.ret)
        for p in fn.params:
            p.ty = self.resolve_type(p.type)
            self.binding_type(p.ty, p)
        fn.max_us = None
        if fn.max_block is not None:
            self.ctx = Ctx('global')
            fn.max_us = self.duration_const(fn.max_block, '`blocking(max: ...)`')
            self.ctx = None

    def expr_safe(self, e, ty):
        saved = self.ctx
        if self.ctx is None:
            self.ctx = Ctx('global')
        t = self.expr(e, ty)
        self.ctx = saved
        return t

    def binding_type(self, ty, node):
        if isinstance(ty, (T.ArrayTy, T.StructTy)):
            self.err('bindings take numbers, `bool`, `str`, enums or library objects', node,
                     help='pass the fields one by one')

    def class_ctor(self, e, cty):
        e.res, e.target = 'class_ctor', cty
        if getattr(self, 'allow_ctor', None) is not e:
            self.err(f'`{cty.name}` objects are created at the top level of a file', e,
                     help=f'write `var name = {cty.name}(...)` outside any function')
        params = cty.ctor or []
        if cty.ctor is None and e.args:
            self.err(f'`{cty.name}` has no `new(...)` in its binding', e)
        return self.bind_args(e, params, cty.decl, cty)

    def use(self, it):
        path = '.'.join(it.path)
        if path == 'c.math':
            name = it.alias or 'math'
            self.declare(Sym('module', name, T.SpecialTy('module math'), it), it)
            self.info.uses_math = True
        elif it.path[0] == 'std':
            self.err(f'module `{path}` is not available yet', it,
                     help='this compiler version ships `c.math`; more modules are on the roadmap')
        else:
            target = self.by_name.get(path)
            if target is None:
                self.err(f'cannot find module `{path}`', it)
                return
            alias = it.alias or it.path[-1]
            msym = Sym('umodule', alias, T.SpecialTy(f'module {path}'), it)
            msym.module = target
            self.declare(msym, it)
            for name in it.names or []:
                s = target.scope.get(name)
                if s is None:
                    self.err(f'module `{path}` has no `{name}`', it)
                elif not s.pub:
                    self.err(f'`{name}` is private to module `{path}`', it,
                             help=f'mark it `pub` in {path.replace(".", "/")}.iot')
                else:
                    self.mod.scope.setdefault(name, s)

    def order_structs(self, items):
        decls = {it.name: self.lookup(it.name).ty for it in items if isinstance(it, N.StructDecl)}
        order, state = [], {}

        def deps(t):
            if isinstance(t, T.StructTy):
                return [t]
            if isinstance(t, T.ArrayTy):
                return deps(t.elem)
            return []

        def visit(sty, node):
            st = state.get(id(sty))
            if st == 'done':
                return
            if st == 'active':
                self.err(f'struct `{sty.name}` contains itself', node,
                         help='a struct cannot hold itself by value')
                return
            state[id(sty)] = 'active'
            for fty, f in sty.fields.values():
                for d in deps(fty):
                    visit(d, f)
            state[id(sty)] = 'done'
            order.append(sty)

        for sty in decls.values():
            visit(sty, sty.decl)
        return order

    def resolve_type(self, tr):
        if tr is None:
            return None
        if isinstance(tr, N.ArrayTypeRef):
            elem = self.resolve_type(tr.elem)
            if isinstance(elem, T.ClassTy):
                self.err('arrays of library objects are not supported', tr)
            size = self.const_int(tr.size, 'array size')
            if size is not None and size <= 0:
                self.err('array size must be at least 1', tr.size)
                size = 1
            return T.ArrayTy(elem, size if size is not None else 1)
        if tr.name in T.PRIMS:
            return T.PRIMS[tr.name]
        if '.' in tr.name:
            mod_name, _, tname = tr.name.rpartition('.')
            m = self.lookup(mod_name)
            if m is None or m.kind != 'umodule':
                return self.err(f'unknown module `{mod_name}`', tr)
            s = self.module_member(m, tname, tr)
            if s is None:
                return ERR
            if s.kind != 'type':
                return self.err(f'`{tr.name}` is not a type', tr)
            return s.ty
        s = self.lookup(tr.name)
        if s is not None and s.kind == 'type':
            return s.ty
        hint = {'int': 'i32', 'float': 'f32', 'double': 'f64', 'string': 'str', 'String': 'str',
                'char': 'u8', 'long': 'i64', 'uint8_t': 'u8', 'uint16_t': 'u16',
                'uint32_t': 'u32', 'int32_t': 'i32', 'boolean': 'bool'}.get(tr.name)
        return self.err(f'unknown type `{tr.name}`', tr, help=f'did you mean `{hint}`?' if hint else None)

    def fn_signature(self, fn):
        fn.ret_ty = self.resolve_type(fn.ret) if fn.ret is not None else T.VOID
        if isinstance(fn.ret_ty, T.ClassTy):
            self.err('functions cannot return library objects', fn.ret)
        seen = set()
        for p in fn.params:
            if p.name in seen:
                self.err(f'parameter `{p.name}` appears twice', p)
            seen.add(p.name)
            p.ty = self.resolve_type(p.type)
            if p.default is not None and p.mutable:
                self.err('a `mut` parameter cannot have a default', p)

    def pin(self, p):
        info = self.info
        chip = info.chip
        gpio = self.const_int(p.number, 'pin number')
        p.gpio = gpio
        p.sym = self.declare(Sym('pin', p.name, T.PinTy(p.kind), p), p)
        if gpio is not None:
            if gpio not in chip.gpios:
                self.err(f'GPIO {gpio} does not exist on the {chip.name}', p.number)
            elif gpio in chip.flash:
                self.err(f'GPIO {gpio} is wired to the flash chip on the {chip.name}', p.number,
                         help='using it crashes the board; pick another pin')
            elif gpio in chip.input_only and p.kind in ('out', 'pwm'):
                self.err(f'GPIO {gpio} is input-only on the {chip.name}', p.number,
                         help='outputs need a pin outside ' +
                              f'{min(chip.input_only)}-{max(chip.input_only)}')
            elif p.kind == 'adc' and gpio not in chip.adc:
                self.err(f'GPIO {gpio} has no ADC on the {chip.name}', p.number,
                         help='ADC pins: ' + ', '.join(str(g) for g in sorted(chip.adc)))
            for other in info.pins:
                if other.gpio == gpio:
                    self.err(f'GPIO {gpio} is already used by pin `{other.name}` (line {other.line})',
                             p.number)
        allowed = PIN_OPTIONS[p.kind]
        opts = {}
        for name, (value, line, col) in p.options.items():
            where = N.Pass()
            where.line, where.col = line, col
            if name not in allowed:
                self.err(f'`{p.kind}` pins have no option `{name}`', where,
                         help='options: ' + (', '.join(allowed) or 'none'))
                continue
            kind, choices = allowed[name]
            vk, v = value
            if kind == 'word':
                if vk != 'word' or v not in choices:
                    self.err(f'`{name}` must be one of: ' + ', '.join(sorted(choices)), where)
                    continue
                opts[name] = v
            elif kind == 'duration':
                t = self.const_typed(v, T.DURATION) if vk == 'expr' else None
                if t is None:
                    self.err(f'`{name}` needs a time such as `30ms`', where)
                    continue
                opts[name] = t
            elif kind == 'freq':
                t = self.const_typed(v, T.FREQUENCY) if vk == 'expr' else None
                if t is None or t <= 0:
                    self.err(f'`{name}` needs a frequency such as `5kHz`', where)
                    continue
                opts[name] = t
            elif kind == 'int':
                t = self.const_int(v, name) if vk == 'expr' else None
                if t is None:
                    continue
                opts[name] = t
            elif kind == 'db':
                ok = vk == 'expr' and isinstance(v, (N.IntLit, N.FloatLit)) and v.unit == 'db'
                if not ok or v.value not in choices:
                    self.err('`atten` must be 0db, 2.5db, 6db or 11db', where)
                    continue
                opts[name] = v.value
        if p.kind == 'pwm':
            bits = opts.setdefault('bits', 8)
            freq = opts.setdefault('freq', 5000)
            if not 1 <= bits <= chip.pwm_bits:
                self.err(f'PWM resolution on the {chip.name} is 1 to {chip.pwm_bits} bits', p)
            elif freq * (1 << bits) > 80_000_000:
                best = 80_000_000 >> bits
                self.err(f'{freq} Hz at {bits} bits needs a {freq * (1 << bits) // 1_000_000} MHz '
                         f'clock; the LEDC runs at 80 MHz', p,
                         help=f'at {bits} bits the highest frequency is {best} Hz; '
                              'lower `bits` or `freq`')
            pwm_count = sum(1 for o in info.pins if o.kind == 'pwm')
            if pwm_count >= chip.ledc_channels // 2:
                self.err(f'the {chip.name} has room for {chip.ledc_channels // 2} independent PWM '
                         'pins', p)
            p.channel = pwm_count * 2
        if p.kind == 'in':
            opts.setdefault('pull', 'up')
            opts.setdefault('debounce', 25_000)
        if p.kind == 'out':
            opts.setdefault('active', 'high')
            opts.setdefault('initial', 'off')
        if p.kind == 'adc':
            opts.setdefault('atten', 11)
        p.opts = opts
        info.pins.append(p)

    def wifi(self, w):
        info = self.info
        core = w.core if getattr(w, 'core', None) is not None else DEFAULT_CORE
        w.core = core
        info.cores_used.add(core)
        if info.chip.name == 'esp32h2':
            self.err('the esp32h2 has no WiFi radio', w)
        opts = {}
        for key, (value, line, col) in w.options.items():
            where = N.Pass()
            where.line, where.col = line, col
            opts[key] = (value, where)
        mode = 'sta'
        if 'mode' in opts:
            value, where = opts['mode']
            if isinstance(value, N.Name) and value.name in ('sta', 'ap'):
                mode = value.name
            else:
                self.err('`mode` is `sta` (join a network) or `ap` (be one)', where)
        w.mode = mode
        for key, (value, where) in opts.items():
            if key not in WIFI_OPTIONS[mode]:
                self.err(f'a WiFi {"station" if mode == "sta" else "access point"} has no setting '
                         f'`{key}`', where, help='settings: ' + ', '.join(WIFI_OPTIONS[mode][1:]))
        if 'ssid' not in opts:
            self.err(f'`wifi {w.name}` needs an `ssid`', w, help='add `ssid: "MyNetwork"`')
            w.ssid = ''
        else:
            w.ssid = self.wifi_text(*opts['ssid'], 'ssid')
            if w.ssid is not None and not 1 <= len(w.ssid.encode()) <= 32:
                self.err('a WiFi name (SSID) is 1 to 32 bytes', opts['ssid'][1])
        w.password = None
        if 'password' in opts:
            w.password = self.wifi_text(*opts['password'], 'password')
            if w.password is not None and not 8 <= len(w.password.encode()) <= 63:
                self.err('WPA2 passwords are 8 to 63 characters', opts['password'][1],
                         help='leave `password` out for an open network')
        w.hostname = None
        if 'hostname' in opts:
            w.hostname = self.wifi_text(*opts['hostname'], 'hostname')
            if w.hostname is not None and not re.fullmatch(r'[A-Za-z0-9]([A-Za-z0-9-]{0,30}[A-Za-z0-9])?',
                                                           w.hostname):
                self.err('a hostname is 1 to 32 letters, digits and `-`, not starting or ending '
                         'with `-`', opts['hostname'][1])
        w.retry = ('backoff', 1000, 60_000)
        if 'retry' in opts:
            w.retry = self.wifi_retry(*opts['retry']) or w.retry
        w.timeout_ms = 15_000
        if 'timeout' in opts:
            v = self.duration_const(opts['timeout'][0], 'timeout', positive=True)
            if v is not None:
                if v < 1_000_000:
                    self.err('give the connection at least 1s', opts['timeout'][1])
                w.timeout_ms = v // 1000
        w.power_save = None
        if 'power_save' in opts:
            value, where = opts['power_save']
            if isinstance(value, N.Name) and value.name in ('none', 'modem', 'max'):
                w.power_save = value.name
            else:
                self.err('`power_save` is `none`, `modem` or `max`', where)
        w.autoconnect = True
        if 'autoconnect' in opts:
            value, where = opts['autoconnect']
            if isinstance(value, N.BoolLit):
                w.autoconnect = value.value
            else:
                self.err('`autoconnect` is `true` or `false`', where)
        w.channel, w.max_clients = 1, 4
        for key, lo, hi in (('channel', 1, 13), ('max_clients', 1, 10)):
            if key in opts:
                v = self.const_int(opts[key][0], key)
                if v is not None:
                    if not lo <= v <= hi:
                        self.err(f'`{key}` is {lo} to {hi}', opts[key][1])
                    setattr(w, key, v)
        for other in info.wifi:
            if other.mode == mode:
                what = 'station' if mode == 'sta' else 'access point'
                self.err(f'the radio has one WiFi {what}; `{other.name}` is already one '
                         f'(line {other.line})', w)
            elif other.core != core:
                self.err(f'`{w.name}` and `{other.name}` share the radio, so they must be on the '
                         'same core', w)
        if mode == 'sta':
            self.wifi_state_sym()      # the C++ state variable needs the enum
        w.cname = f'iot_wifi_{self.mod.prefix}{w.name}'
        w.sym = self.declare(Sym('wifi', w.name, T.SpecialTy('wifi'), w, core=core), w)
        info.wifi.append(w)

    def wifi_text(self, value, where, what):
        """Text for a WiFi setting: a plain string or `secret("NAME")`."""
        if isinstance(value, N.StrLit) and all(isinstance(p, str) for p in value.parts):
            return ''.join(value.parts)
        if isinstance(value, N.Call) and isinstance(value.callee, N.Name) and \
                value.callee.name == 'secret' and len(value.args) == 1 and \
                isinstance(value.args[0].value, N.StrLit) and \
                all(isinstance(p, str) for p in value.args[0].value.parts):
            name = ''.join(value.args[0].value.parts)
            text = self.secret(name)
            if text is None:
                self.err(f'secret `{name}` is not set', value,
                         help=f'set the environment variable {name}, or add the line `{name}=...` '
                              'to a `.env` file next to the main file')
            return text
        self.err(f'`{what}` must be text in quotes or `secret("NAME")`', where)
        return None

    def secret(self, name):
        """A build-time secret: the environment first, then `.env` next to the main file."""
        if name in os.environ:
            return os.environ[name]
        if self.root:
            path = os.path.join(self.root, '.env')
            if os.path.isfile(path):
                with open(path, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        k, v = line.split('=', 1)
                        if k.strip() == name:
                            v = v.strip()
                            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                                v = v[1:-1]
                            return v
        return None

    def wifi_retry(self, value, where):
        if isinstance(value, N.Name) and value.name in ('none', 'forever'):
            return ('none',) if value.name == 'none' else ('backoff', 1000, 60_000)
        if isinstance(value, N.Call) and isinstance(value.callee, N.Name):
            kind, args = value.callee.name, value.args
            if kind == 'fixed' and len(args) == 1 and args[0].name is None:
                v = self.duration_const(args[0].value, 'retry delay', positive=True)
                return ('fixed', max(v // 1000, 1)) if v is not None else None
            if kind == 'backoff' and len(args) == 2 and args[0].name is None and args[1].name == 'max':
                base = self.duration_const(args[0].value, 'first retry delay', positive=True)
                top = self.duration_const(args[1].value, 'longest retry delay', positive=True)
                if base is None or top is None:
                    return None
                if top < base:
                    self.err('`max` must be at least the first delay', args[1].value)
                    return None
                return ('backoff', max(base // 1000, 1), max(top // 1000, 1))
        self.err('`retry` is `none`, `forever`, `fixed(5s)` or `backoff(1s, max: 1min)`', where)
        return None

    def check_wifi_adc(self):
        info = self.info
        if info.chip.name != 'esp32' or not info.wifi:
            return
        for p in info.pins:
            if p.kind == 'adc' and p.gpio in ESP32_ADC2:
                saved, self.file = self.file, getattr(p, 'file', self.file)
                self.err(f'GPIO {p.gpio} is on ADC2, which the esp32 cannot read while WiFi is on',
                         p.number, help='ADC1 pins: 32, 33, 34, 35, 36, 39')
                self.file = saved

    def global_decl(self, g):
        core = g.core if g.core is not None else DEFAULT_CORE
        self.ctx = Ctx('global', core=core)
        ty = self.resolve_type(g.type) if g.type is not None else None
        if g.kind == 'shared':
            if ty is None:
                self.err('a `shared var` needs a type', g, help=f'write `shared var {g.name}: i32 = 0`')
                ty = ERR
            elif ty not in (T.BOOL, T.F32, T.I8, T.I16, T.I32, T.U8, T.U16, T.U32, ERR):
                self.err(f'`shared var` holds bool, f32 or an integer up to 32 bits, not `{ty}`', g,
                         help='larger shared data arrives with channels in the next milestone')
            if g.core is not None:
                self.err('`shared var` belongs to both cores; declare it outside `core` blocks', g)
        if g.init is None:
            if g.kind in ('let', 'const'):
                self.err(f'`{g.kind} {g.name}` needs a value', g)
            if ty is None:
                self.err(f'`{g.name}` needs a type or a value', g, help=f'write `var {g.name}: i32 = 0`')
                ty = ERR
        else:
            self.no_calls(g.init)
            self.allow_ctor = g.init
            t = self.expr(g.init, ty)
            self.allow_ctor = None
            if ty is None:
                ty = t
                if g.kind == 'const' and T.is_untyped(t):
                    pass  # untyped constant: adapts to where it is used
                else:
                    ty = T.default_of(t)
                    self.settle(g.init, ty)
            else:
                self.coerce(g.init, t, ty)
        if isinstance(ty, T.ClassTy):
            if g.kind not in ('var', 'let'):
                self.err(f'library objects are declared with `var`', g)
            if g.init is None and ty.ctor:
                self.err(f'`{ty.name}` needs constructor arguments', g,
                         help=f'write `var {g.name} = {ty.name}(...)`')
            if g.init is not None and not (isinstance(g.init, N.Call) and g.init.res == 'class_ctor'):
                self.err('library objects cannot be copied', g.init,
                         help=f'create it here with `{ty.name}(...)`')
        kind = {'let': 'let', 'var': 'var', 'const': 'const', 'shared': 'shared'}[g.kind]
        sym = Sym(kind, g.name, ty, g, mutable=(g.kind == 'var'), core=core)
        if g.kind == 'const':
            if g.init is not None:
                sym.const_value = self.const_eval(g.init)
                if sym.const_value is None and g.init is not None:
                    self.err(f'`const {g.name}` must be known at compile time', g.init,
                             help='use `let` for values computed at run time')
        g.sym = self.declare(sym, g)
        g.ty = ty
        self.info.globals.append(g)
        self.ctx = None

    def no_calls(self, e):
        for sub in walk_expr(e):
            if isinstance(sub, N.Call):
                callee = sub.callee
                if isinstance(callee, N.Name) or self.is_qualified(callee):
                    s = self.static_ref(callee)
                    if s is ERROR_SYM or (s is not None and s.kind == 'type'):
                        continue
                    if isinstance(callee, N.Name) and s is None and callee.name in ('abs', 'min', 'max'):
                        continue
                self.err('global values cannot call functions', sub,
                         help='set it in `on boot do ... end` instead')

    # ── constants ──────────────────────────────────────────────────────

    def const_eval(self, e):
        if isinstance(e, N.IntLit):
            if e.unit in DURATION_UNITS:
                return e.value * DURATION_UNITS[e.unit]
            if e.unit in FREQ_UNITS:
                return e.value * FREQ_UNITS[e.unit]
            if e.unit in SIZE_UNITS:
                return e.value * SIZE_UNITS[e.unit]
            return e.value
        if isinstance(e, N.FloatLit):
            if e.unit in DURATION_UNITS:
                return int(round(e.value * DURATION_UNITS[e.unit]))
            if e.unit in FREQ_UNITS:
                return int(round(e.value * FREQ_UNITS[e.unit]))
            return e.value
        if isinstance(e, N.BoolLit):
            return e.value
        if isinstance(e, N.Name):
            s = self.lookup(e.name)
            if s is not None and s.kind == 'const':
                return s.const_value
            return None
        if isinstance(e, N.Unary):
            v = self.const_eval(e.operand)
            if v is None or isinstance(v, bool):
                return None
            if e.op == '-':
                return -v
            if e.op == '~' and isinstance(v, int):
                return ~v
            return None
        if isinstance(e, N.Binary):
            a, b = self.const_eval(e.left), self.const_eval(e.right)
            if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
                return None
            try:
                if e.op == '+':
                    return a + b
                if e.op == '-':
                    return a - b
                if e.op == '*':
                    return a * b
                if e.op == '/':
                    if isinstance(a, int) and isinstance(b, int):
                        return int(a / b)
                    return a / b
                if e.op == '%' and isinstance(a, int) and isinstance(b, int):
                    return a - b * int(a / b)
                if isinstance(a, int) and isinstance(b, int):
                    return {'<<': lambda: a << b, '>>': lambda: a >> b, '&': lambda: a & b,
                            '|': lambda: a | b, '^': lambda: a ^ b}.get(e.op, lambda: None)()
            except (ZeroDivisionError, ValueError):
                return None
        if isinstance(e, N.Cast):
            return self.const_eval(e.expr)
        return None

    def const_int(self, e, what):
        saved = self.ctx
        if self.ctx is None:
            self.ctx = Ctx('global')
        t = self.expr(e, None)
        self.ctx = saved
        v = self.const_eval(e)
        if t is ERR:
            return None
        if not T.is_int(t) or v is None or isinstance(v, float):
            self.err(f'{what} must be a whole number known at compile time', e)
            return None
        self.settle(e, T.default_of(t))
        return v

    def const_typed(self, e, ty):
        saved = self.ctx
        if self.ctx is None:
            self.ctx = Ctx('global')
        t = self.expr(e, ty)
        self.ctx = saved
        if t != ty:
            return None
        return self.const_eval(e)

    # ── pass 2: bodies ─────────────────────────────────────────────────

    def check_item(self, it):
        info = self.info
        core = getattr(it, 'core', None)
        core = core if core is not None else DEFAULT_CORE
        if isinstance(it, N.FnDecl):
            self.fn_body(it)
        elif isinstance(it, N.ImplDecl):
            for fn in it.fns:
                if getattr(fn, 'ret_ty', None) is not None:
                    self.fn_body(fn)
        elif isinstance(it, N.Every):
            it.core = core
            info.cores_used.add(core)
            self.ctx = Ctx('handler', core=core, label='this timer')
            it.period_us = self.duration_const(it.period, 'timer period', positive=True)
            it.offset_us = self.duration_const(it.offset, 'offset') if it.offset is not None else None
            self.body(it.body)
            self.finish_ctx()
            info.timers.append(it)
        elif isinstance(it, N.After):
            it.core = core
            info.cores_used.add(core)
            self.ctx = Ctx('handler', core=core, label='this `after` block')
            it.delay_us = self.duration_const(it.delay, 'delay')
            self.body(it.body)
            self.finish_ctx()
            info.afters.append(it)
        elif isinstance(it, N.OnEvent):
            src = self.lookup(it.source.name) if it.source is not None else None
            if src is not None and src.kind == 'wifi':
                # WiFi events run where the WiFi lives unless a `core` block says otherwise.
                if getattr(it, 'core', None) is None:
                    core = src.core
                elif it.core != src.core:
                    self.err(f'`{src.name}` runs on core {src.core}, so its events do too', it,
                             help=f'move this handler out of the `core {it.core}` block')
            it.core = core
            info.cores_used.add(core)
            self.ctx = Ctx('handler', core=core, label='this handler')
            if it.source is None:
                self.ctx.boot = True
                self.ctx.label = f'`on {it.event}`'
                info.system[it.event].append(it)
            else:
                s = src
                ev_at = N.Pass()
                ev_at.line, ev_at.col = getattr(it, 'event_tok', (it.line, it.col))
                if s is None:
                    self.err(f'unknown pin or WiFi `{it.source.name}`', it.source)
                elif s.kind == 'wifi':
                    events = WIFI_EVENTS[s.decl.mode]
                    if it.event not in events:
                        self.err(f'`{s.name}` has no event `{it.event}`', ev_at,
                                 help='events: ' + ', '.join(events))
                    else:
                        it.wifi = s.decl
                        info.wifi_events.append(it)
                elif s.kind != 'pin':
                    self.err(f'`{it.source.name}` is not a pin', it.source)
                elif it.event not in PIN_EVENTS:
                    self.err(f'unknown pin event `{it.event}`', ev_at,
                             help='events: ' + ', '.join(sorted(PIN_EVENTS)))
                elif s.ty.kind != 'in':
                    self.err(f'`.{it.event}` events need an `in` pin; `{s.name}` is `{s.ty.kind}`',
                             it.source)
                else:
                    it.pin = s.decl
                    info.events.append(it)
            self.body(it.body)
            self.finish_ctx()
        elif isinstance(it, N.TaskDecl):
            core = it.core if it.core is not None else DEFAULT_CORE
            it.core = core
            info.cores_used.add(core)
            self.ctx = Ctx('task', core=core, task=it, label=f'task `{it.name}`')
            it.locals = []
            self.body(it.body)
            self.finish_ctx()
            info.tasks.append(it)
        self.ctx = None

    def finish_ctx(self):
        self.contexts.append(self.ctx)

    def duration_const(self, e, what, positive=False):
        t = self.expr(e, T.DURATION)
        if t is ERR:
            return 0
        if t != T.DURATION:
            if T.is_num(t):
                self.err(f'{what} needs a unit', e, help='write it as a time, e.g. `500ms` or `2s`')
                return 0
            self.err(f'{what} must be a time, found `{t}`', e)
            return 0
        v = self.const_eval(e)
        if v is None:
            self.err(f'{what} must be known at compile time', e)
            return 0
        if positive and v <= 0:
            self.err(f'{what} must be longer than zero', e)
        if v % 1000 and v > 0:
            self.err(f'{what} must be a whole number of milliseconds for now', e)
        return v

    def fn_body(self, fn):
        owner = fn.owner if isinstance(fn.owner, T.StructTy) else None
        self.ctx = Ctx('fn', ret=fn.ret_ty, fn=fn, self_ty=owner if fn.self_kind else None,
                       self_mut=fn.self_kind == 'mut self', label=f'`{fn.name}`')
        self.push()
        for p in fn.params:
            p.sym = self.declare(Sym('param', p.name, p.ty, p, mutable=p.mutable), p)
            if p.default is not None:
                t = self.expr(p.default, p.ty)
                self.coerce(p.default, t, p.ty)
        self.stmts(fn.body)
        self.pop()
        if fn.ret_ty not in (T.VOID, ERR) and not always_returns(fn.body):
            self.err(f'`{fn.name}` must return a `{fn.ret_ty}` on every path', fn,
                     help='add a `return` at the end')
        if fn.sym is None:
            fn.sym = Sym('fn', fn.name, None, fn)
        self.fn_ctx[fn.sym] = self.ctx
        self.ctx = None

    def body(self, stmts):
        self.push()
        self.stmts(stmts)
        self.pop()

    def stmts(self, stmts):
        for s in stmts:
            self.stmt(s)

    def stmt(self, s):
        ctx = self.ctx
        if isinstance(s, N.VarDecl):
            ty = self.resolve_type(s.type) if s.type is not None else None
            if s.init is None:
                if not s.mutable:
                    self.err(f'`let {s.name}` needs a value', s)
                if ty is None:
                    self.err(f'`{s.name}` needs a type or a value', s,
                             help=f'write `var {s.name}: i32 = 0`')
                    ty = ERR
            else:
                t = self.expr(s.init, ty)
                if isinstance(s.init, N.StrLit) and self.has_interp(s.init):
                    self.err('formatted strings can only be printed for now', s.init,
                             help='pass it straight to `print` or `println`')
                if t == T.VOID:
                    self.err('this expression has no value', s.init)
                    t = ERR
                if ty is None:
                    ty = T.default_of(t)
                    self.settle(s.init, ty)
                else:
                    self.coerce(s.init, t, ty)
            if isinstance(ty, T.ClassTy):
                self.err(f'`{ty.name}` objects are created at the top level of a file', s,
                         help='declare it next to your pins and pass it to functions that need it')
            sym = Sym('local', s.name, ty, s, mutable=s.mutable)
            sym.task = ctx.task
            if ctx.task is not None:
                ctx.task.locals.append(sym)
            s.sym = self.declare(sym, s)
        elif isinstance(s, N.Assign):
            self.assign(s)
        elif isinstance(s, N.ExprStmt):
            t = self.expr(s.expr, None)
            if not isinstance(s.expr, N.Call) and t is not ERR:
                hint = None
                if isinstance(s.expr, N.Binary) and s.expr.op == '==':
                    hint = 'use `=` to assign'
                elif isinstance(s.expr, N.Member) and isinstance(s.expr.obj, N.Name):
                    hint = f'did you mean `{s.expr.obj.name}.{s.expr.name}()`?'
                self.err('this expression does nothing', s.expr, help=hint)
            else:
                self.settle(s.expr, T.default_of(t))
        elif isinstance(s, N.If):
            for cond, body in s.branches:
                self.cond(cond)
                self.body(body)
            if s.else_body is not None:
                self.body(s.else_body)
        elif isinstance(s, N.While):
            self.cond(s.cond)
            ctx.loops += 1
            self.body(s.body)
            ctx.loops -= 1
        elif isinstance(s, N.Loop):
            ctx.loops += 1
            self.body(s.body)
            ctx.loops -= 1
            if ctx.kind == 'task':
                if not any(isinstance(x, N.Wait) for x in walk_stmts(s.body)):
                    self.err(f'this `loop` never waits, so it would freeze core {ctx.core}', s,
                             help='add a `wait`, for example `wait 10ms`')
            elif not has_exit(s.body):
                self.err(f'this `loop` never ends; it would freeze the core', s,
                         help='loops that run forever belong in a `task` with a `wait`')
        elif isinstance(s, N.For):
            self.for_stmt(s)
        elif isinstance(s, N.Match):
            self.match(s)
        elif isinstance(s, (N.Break, N.Continue)):
            if ctx.loops == 0:
                self.err(f'`{"break" if isinstance(s, N.Break) else "continue"}` outside a loop', s)
        elif isinstance(s, N.Pass):
            pass
        elif isinstance(s, N.Return):
            if s.value is None:
                if ctx.kind == 'fn' and ctx.ret not in (T.VOID, ERR):
                    self.err(f'this function returns `{ctx.ret}`; give `return` a value', s)
            else:
                if ctx.kind != 'fn' or ctx.ret == T.VOID:
                    self.err('this block does not return a value', s.value,
                             help='write `return` on its own')
                    self.expr(s.value, None)
                else:
                    t = self.expr(s.value, ctx.ret)
                    self.coerce(s.value, t, ctx.ret)
        elif isinstance(s, N.Wait):
            if ctx.kind != 'task':
                self.err('`wait` can only be used inside a `task`', s,
                         help='functions and event handlers run to completion; '
                              'put sequences with waits in a `task`')
            if s.kind == 'for':
                t = self.expr(s.expr, T.DURATION)
                if t not in (T.DURATION, ERR):
                    if T.is_num(t):
                        self.err('`wait` needs a time with a unit', s.expr,
                                 help='for example `wait 500ms`')
                    else:
                        self.err(f'`wait` needs a time, found `{t}`', s.expr,
                                 help='use `wait until <condition>` to wait for a condition')
            else:
                self.cond(s.expr)
                if s.timeout is not None:
                    t = self.expr(s.timeout, T.DURATION)
                    if t not in (T.DURATION, ERR):
                        self.err('`timeout` needs a time such as `5s`', s.timeout)
            if ctx.task is not None:
                ctx.task.waits = getattr(ctx.task, 'waits', 0) + 1
        else:
            self.err('unsupported statement', s)

    def has_interp(self, s):
        return any(isinstance(p, N.Interpolation) for p in s.parts)

    def cond(self, e):
        t = self.expr(e, T.BOOL)
        if t not in (T.BOOL, ERR):
            hint = None
            if T.is_int(t):
                hint = 'compare it explicitly, e.g. `x != 0`'
            self.err(f'condition must be `bool`, found `{t}`', e, help=hint)

    def for_stmt(self, s):
        ctx = self.ctx
        self.push()
        if isinstance(s.iter, N.RangeExpr):
            lt = self.expr(s.iter.lo, None)
            ht = self.expr(s.iter.hi, None)
            ty = self.unify(lt, ht, s.iter.lo, s.iter.hi, s.iter)
            if ty is not ERR and not T.is_int(ty):
                self.err(f'ranges are over integers, not `{ty}`', s.iter)
                ty = ERR
            ty = T.default_of(ty)
            self.settle(s.iter.lo, ty)
            self.settle(s.iter.hi, ty)
            s.iter.ty = ty
            elem = ty
            if s.step is not None:
                st = self.expr(s.step, ty)
                self.coerce(s.step, st, ty)
                v = self.const_eval(s.step)
                if v is None:
                    self.err('the `by` step must be known at compile time', s.step)
                elif v <= 0:
                    self.err('the `by` step must be positive', s.step,
                             help='count down with a while loop')
        else:
            if s.step is not None:
                self.err('`by` only works with ranges such as `0..10`', s.step)
            t = self.expr(s.iter, None)
            if isinstance(t, T.ArrayTy):
                elem = t.elem
            else:
                if t is not ERR:
                    self.err(f'cannot loop over `{t}`', s.iter, help='loop over a range (`0..10`) or an array')
                elem = ERR
        sym = Sym('local', s.var, elem, s, mutable=False)
        sym.task = ctx.task
        if ctx.task is not None:
            ctx.task.locals.append(sym)
        s.sym = self.declare(sym, s)
        ctx.loops += 1
        self.stmts(s.body)
        ctx.loops -= 1
        self.pop()

    def match(self, s):
        t = self.expr(s.subject, None)
        t = T.default_of(t)
        self.settle(s.subject, t)
        covered = set()
        has_wild = False
        for arm in s.arms:
            for p in arm.patterns:
                if isinstance(p, N.WildcardPat):
                    has_wild = True
                    continue
                if isinstance(t, T.EnumTy) and isinstance(p, N.Name) and p.name in t.variants and \
                        self.lookup(p.name) is None:
                    p.res = 'variant'
                    p.ty = t
                    p.target = p.name
                    if arm.guard is None:
                        covered.add(p.name)
                    continue
                if isinstance(p, N.RangeExpr):
                    for part in (p.lo, p.hi):
                        pt = self.expr(part, t)
                        self.coerce(part, pt, t)
                        if self.const_eval(part) is None:
                            self.err('range patterns need constant bounds', part)
                    if t is not ERR and not T.is_int(t):
                        self.err(f'range patterns need an integer, not `{t}`', p)
                    continue
                pt = self.expr(p, t)
                self.coerce(p, pt, t)
                if isinstance(t, T.EnumTy) and getattr(p, 'res', None) == 'variant':
                    if arm.guard is None:
                        covered.add(p.target)
                elif t == T.BOOL and isinstance(p, N.BoolLit):
                    if arm.guard is None:
                        covered.add(p.value)
                elif self.const_eval(p) is None and not isinstance(p, N.StrLit):
                    self.err('patterns must be constants', p)
            if arm.guard is not None:
                self.cond(arm.guard)
            self.body(arm.body)
        if t is ERR or has_wild:
            s.exhaustive = True
            return
        if isinstance(t, T.EnumTy):
            missing = [v for v in t.variants if v not in covered]
            if missing:
                self.err(f'`match` does not handle ' + ', '.join(f'`{t.name}.{m}`' for m in missing),
                         s, help='add the missing arms or a `_ => ...` arm')
        elif t == T.BOOL:
            if covered != {True, False}:
                self.err('`match` must handle both `true` and `false`', s)
        else:
            self.err(f'`match` on `{t}` needs a `_ => ...` arm for the other values', s)
        s.exhaustive = True

    def assign(self, s):
        place = s.place
        t = self.expr(place, None)
        self.check_place(place)
        if isinstance(t, T.ClassTy):
            self.err('library objects cannot be copied or reassigned', s)
            return
        if s.op == '=':
            vt = self.expr(s.value, t if t is not ERR else None)
            if isinstance(s.value, N.StrLit) and self.has_interp(s.value):
                self.err('formatted strings can only be printed for now', s.value)
            self.coerce(s.value, vt, t)
        else:
            op = s.op[:-1]
            vt = self.expr(s.value, t)
            fake = N.Binary(op, place, s.value)
            fake.line, fake.col = s.line, s.col
            rt = self.binary_types(fake, op, t, vt)
            if rt is not ERR and t is not ERR and rt != t and not T.is_untyped(rt):
                self.coerce(s.value, rt, t)
            self.settle(s.value, t if t is not ERR else T.default_of(vt))

    def check_place(self, e):
        root = e
        while isinstance(root, (N.Member, N.Index)):
            if isinstance(root, N.Member) and root.res != 'field':
                return self.err('this cannot be assigned to', e)
            root = root.obj
        if isinstance(root, N.SelfRef):
            if not self.ctx.self_mut:
                self.err('cannot change `self` here', e, help='declare the method with `mut self`')
            return
        if not isinstance(root, N.Name) or root.ref is None:
            if not isinstance(root, N.Name):
                self.err('this cannot be assigned to', e)
            return
        sym = root.ref
        if sym.kind == 'pin':
            hint = {'out': f'`{sym.name}.on()`, `{sym.name}.off()` or `{sym.name}.set(value)`',
                    'pwm': f'`{sym.name}.duty(0.5)` or `{sym.name}.write(n)`'}.get(sym.ty.kind)
            self.err(f'pins are changed with methods, not `=`', e, help=f'use {hint}' if hint else None)
        elif sym.kind == 'shared':
            self.err(f'`{sym.name}` is shared between cores', e, help=f'use `{sym.name}.set(value)`')
        elif not sym.mutable:
            what = {'const': 'a constant', 'let': 'immutable', 'local': 'immutable',
                    'param': 'a parameter'}.get(sym.kind, 'not assignable')
            help = None
            if sym.kind in ('let', 'local'):
                help = f'declare it with `var {sym.name}` to change it'
            elif sym.kind == 'param':
                help = f'declare the parameter as `mut {sym.name}` or copy it into a `var`'
            self.err(f'`{sym.name}` is {what}', e, help=help)

    # ── expressions ────────────────────────────────────────────────────

    def expr(self, e, expect):
        t = self.expr_inner(e, expect)
        e.ty = t
        return t

    def expr_inner(self, e, expect):
        if isinstance(e, N.IntLit):
            return self.int_lit(e)
        if isinstance(e, N.FloatLit):
            if e.unit in DURATION_UNITS:
                return T.DURATION
            if e.unit in FREQ_UNITS:
                return T.FREQUENCY
            if e.unit in ('f32', 'f64'):
                return T.PRIMS[e.unit]
            if e.unit is not None:
                return self.err(f'`{e.unit}` is not valid here', e)
            return T.UFLOAT
        if isinstance(e, N.BoolLit):
            return T.BOOL
        if isinstance(e, N.StrLit):
            for p in e.parts:
                if isinstance(p, N.Interpolation):
                    pt = self.expr(p.expr, None)
                    self.settle(p.expr, T.default_of(pt))
                    self.check_printable(p.expr, pt, p.fmt)
            return T.STR
        if isinstance(e, N.Name):
            return self.name(e)
        if isinstance(e, N.SelfRef):
            if self.ctx is None or self.ctx.self_ty is None:
                return self.err('`self` is only available in methods', e)
            return self.ctx.self_ty
        if isinstance(e, N.Unary):
            return self.unary(e, expect)
        if isinstance(e, N.Binary):
            if e.op in ('and', 'or'):
                self.cond(e.left)
                self.cond(e.right)
                return T.BOOL
            lt = self.expr(e.left, expect if e.op not in ('==', '!=', '<', '<=', '>', '>=') else None)
            rt = self.expr(e.right, lt if not T.is_untyped(lt) and lt is not ERR else
                           (expect if e.op not in ('==', '!=', '<', '<=', '>', '>=') else None))
            return self.binary_types(e, e.op, lt, rt)
        if isinstance(e, N.Call):
            return self.call(e, expect)
        if isinstance(e, N.Member):
            return self.member(e)
        if isinstance(e, N.Index):
            ot = self.expr(e.obj, None)
            it = self.expr(e.index, T.USIZE)
            if it is not ERR and not T.is_int(it):
                self.err(f'array index must be an integer, found `{it}`', e.index)
            self.settle(e.index, T.default_of(it))
            if ot is ERR:
                return ERR
            if not isinstance(ot, T.ArrayTy):
                return self.err(f'`{ot}` cannot be indexed', e.obj)
            v = self.const_eval(e.index)
            if isinstance(v, int) and not 0 <= v < ot.size:
                self.err(f'index {v} is out of range for `{ot}` (0 to {ot.size - 1})', e.index)
            return ot.elem
        if isinstance(e, N.Cast):
            src = self.expr(e.expr, None)
            dst = self.resolve_type(e.to)
            if src is ERR or dst is ERR:
                return ERR
            if T.is_untyped(src):
                self.settle(e.expr, T.default_of(src) if not T.is_num(dst) else
                            (dst if (T.is_float(dst) or src == T.UINT) else T.default_of(src)))
                src = e.expr.ty
            ok = (T.is_num(src) and T.is_num(dst)) or (src == T.BOOL and T.is_int(dst)) or \
                 (isinstance(src, T.EnumTy) and T.is_int(dst)) or (src == dst)
            if not ok:
                hint = None
                if src == T.DURATION:
                    hint = 'use `.as_ms()`, `.as_us()` or `.as_s()`'
                elif dst == T.DURATION:
                    hint = 'multiply by a unit, e.g. `n * 1ms`'
                return self.err(f'cannot cast `{src}` to `{dst}`', e, help=hint)
            return dst
        if isinstance(e, N.IfExpr):
            self.cond(e.cond)
            a = self.expr(e.then, expect)
            b = self.expr(e.other, a if not T.is_untyped(a) else expect)
            return self.unify(a, b, e.then, e.other, e)
        if isinstance(e, N.ArrayLit):
            if not e.items:
                return self.err('empty array literal', e, help='declare `var a: [i32; 4]` instead')
            et = expect.elem if isinstance(expect, T.ArrayTy) else None
            types = [self.expr(i, et) for i in e.items]
            if et is None:
                et = types[0]
                for i, ti in zip(e.items[1:], types[1:]):
                    et = self.unify(et, ti, e.items[0], i, i)
                et = T.default_of(et)
            for i, ti in zip(e.items, types):
                self.coerce(i, ti, et)
            if isinstance(expect, T.ArrayTy) and expect.size != len(e.items):
                self.err(f'expected {expect.size} items, found {len(e.items)}', e)
            return T.ArrayTy(et, len(e.items))
        if isinstance(e, N.RangeExpr):
            return self.err('ranges can only be used in `for` loops and `match` patterns', e)
        return self.err('unsupported expression', e)

    def int_lit(self, e):
        if e.unit is None:
            return T.UINT
        if e.unit in DURATION_UNITS:
            return T.DURATION
        if e.unit in FREQ_UNITS:
            return T.FREQUENCY
        if e.unit in SIZE_UNITS:
            return T.UINT
        if e.unit in T.PRIMS and e.unit not in ('str', 'bool', 'Duration', 'Frequency'):
            ty = T.PRIMS[e.unit]
            if T.is_int(ty) and not T.fits(e.value, ty):
                self.err(f'{e.value} does not fit in `{ty}`', e)
            return ty
        return self.err(f'`{e.unit}` is not valid here', e)

    def name(self, e):
        s = self.lookup(e.name)
        if s is None:
            hint = None
            if e.name in ('HIGH', 'LOW'):
                hint = 'use `true` / `false`, or pin methods like `led.on()`'
            elif e.name in ('delay', 'sleep'):
                hint = 'use `wait 100ms` inside a `task`'
            elif e.name == 'math':
                hint = 'add `use c.math` at the top of the file'
            return self.err(f'unknown name `{e.name}`', e, help=hint)
        return self.use_sym(e, s)

    def module_member(self, msym, name, node):
        target = msym.module
        s = target.scope.get(name)
        if s is None:
            self.err(f'module `{target.name}` has no `{name}`', node)
            return None
        if not s.pub:
            self.err(f'`{name}` is private to module `{target.name}`', node,
                     help=f'mark it `pub` in {target.name.replace(".", "/")}.iot')
            return None
        return s

    def is_qualified(self, x):
        if isinstance(x, N.Member) and isinstance(x.obj, N.Name):
            m = self.lookup(x.obj.name)
            return m is not None and m.kind == 'umodule'
        return False

    def static_ref(self, x):
        """The symbol a name or a `module.name` refers to (None if it is
        neither, or ERROR_SYM if it was already reported)."""
        if isinstance(x, N.Name):
            s = self.lookup(x.name)
            if s is not None:
                x.ref = s
            return s
        if self.is_qualified(x):
            m = self.lookup(x.obj.name)
            x.obj.ref = m
            s = self.module_member(m, x.name, x)
            if s is None:
                return ERROR_SYM
            x.ref = s
            x.res, x.target = 'qualified', s
            return s
        return None

    def use_sym(self, e, s):
        e.ref = s
        if s.kind == 'umodule':
            return self.err(f'`{s.name}` is a module; use `{s.name}.<name>`', e)
        if s.kind in ('var',) and self.ctx is not None:
            self.ctx.touches.append((s, e))
        if s.kind == 'shared':
            return self.err(f'`{s.name}` is shared between cores', e,
                            help=f'read it with `{s.name}.get()`')
        if s.kind == 'pin':
            return self.err(f'`{s.name}` is a pin; use one of its methods', e,
                            help='e.g. ' + ', '.join(f'`{s.name}.{m}()`' for m in
                                                     list(PIN_METHODS[s.ty.kind])[:3]))
        if s.kind in ('fn',):
            return self.err(f'`{s.name}` is a function; call it with `{s.name}()`', e)
        if s.kind == 'wifi':
            return self.err(f'`{s.name}` is a WiFi connection, not a value', e,
                            help='use its properties, e.g. ' + ', '.join(
                                f'`{s.name}.{p}`' for p in WIFI_PROPS[s.decl.mode][:3]))
        if s.kind in ('type', 'module', 'timer', 'task'):
            return self.err(f'`{s.name}` is a {s.kind if s.kind != "type" else "type"}, not a value', e)
        return s.ty

    def unary(self, e, expect):
        t = self.expr(e.operand, expect)
        if t is ERR:
            return ERR
        if e.op == 'not':
            if t != T.BOOL:
                return self.err(f'`not` needs a `bool`, found `{t}`', e)
            return T.BOOL
        if e.op == '-':
            if t == T.DURATION or T.is_float(t) or t == T.UINT:
                return t
            if T.is_int(t):
                if not T.INT_INFO[t.name][0]:
                    return self.err(f'cannot negate unsigned `{t}`', e)
                return t
            return self.err(f'cannot negate `{t}`', e)
        if e.op == '~':
            if not T.is_int(t):
                return self.err(f'`~` needs an integer, found `{t}`', e)
            return t
        return self.err('unknown operator', e)

    def binary_types(self, e, op, lt, rt):
        if lt is ERR or rt is ERR:
            return ERR
        left, right = e.left, e.right
        cmp = op in ('==', '!=', '<', '<=', '>', '>=')
        # durations and frequencies
        if T.DURATION in (lt, rt) or T.FREQUENCY in (lt, rt):
            unit = T.DURATION if T.DURATION in (lt, rt) else T.FREQUENCY
            if lt == rt == unit:
                if op in ('+', '-'):
                    return unit
                if cmp:
                    return T.BOOL
                if op == '/' and unit == T.DURATION:
                    return self.err('dividing two times is not supported yet', e,
                                    help='compare `.as_us()` values instead')
            if op in ('*', '/') and lt == unit and T.is_num(rt):
                self.settle(right, T.default_of(rt) if not T.is_untyped(rt) else
                            (T.I64 if rt == T.UINT else T.F32))
                return unit
            if op == '*' and rt == unit and T.is_num(lt):
                self.settle(left, T.default_of(lt) if not T.is_untyped(lt) else
                            (T.I64 if lt == T.UINT else T.F32))
                return unit
            if T.is_num(lt) or T.is_num(rt):
                return self.err(f'cannot mix `{lt}` and `{rt}` with `{op}`', e,
                                help='give the number a unit, e.g. `100ms`')
            return self.err(f'cannot use `{op}` on `{lt}` and `{rt}`', e)
        if op in ('+', '-', '*', '/', '%'):
            if not T.is_num(lt) or not T.is_num(rt):
                bad = lt if not T.is_num(lt) else rt
                hint = 'joining strings comes with the `Text` type; for now print the parts' \
                    if bad == T.STR and op == '+' else None
                return self.err(f'cannot use `{op}` on `{bad}`', e, help=hint)
            t = self.unify(lt, rt, left, right, e)
            if op == '%' and T.is_float(t) and t is not ERR:
                return self.err('`%` needs integers', e, help='use `math.fmod(a, b)` for floats')
            if op in ('/', '%'):
                v = self.const_eval(right)
                if v == 0:
                    self.err('division by zero', right)
            return t
        if op in ('&', '|', '^', '<<', '>>'):
            if not T.is_int(lt) or not T.is_int(rt):
                bad = lt if not T.is_int(lt) else rt
                hint = 'use `and` / `or` for conditions' if bad == T.BOOL else None
                return self.err(f'`{op}` needs integers, found `{bad}`', e, help=hint)
            if op in ('<<', '>>'):
                self.settle(right, T.default_of(rt))
                if T.is_untyped(lt):
                    return lt
                v = self.const_eval(right)
                if isinstance(v, int) and not 0 <= v < T.INT_INFO[lt.name][1]:
                    self.err(f'shifting a `{lt}` by {v} bits', right)
                return lt
            return self.unify(lt, rt, left, right, e)
        if cmp:
            if T.is_num(lt) and T.is_num(rt):
                t = self.unify(lt, rt, left, right, e)
                self.settle(left, T.default_of(t) if t is not ERR else T.I32)
                self.settle(right, T.default_of(t) if t is not ERR else T.I32)
                return T.BOOL
            if lt == rt and (lt in (T.BOOL, T.STR) or isinstance(lt, T.EnumTy)):
                if op not in ('==', '!='):
                    return self.err(f'`{op}` does not apply to `{lt}`', e)
                return T.BOOL
            return self.err(f'cannot compare `{lt}` with `{rt}`', e)
        return self.err(f'unknown operator `{op}`', e)

    def unify(self, a, b, ea, eb, node):
        if a is ERR or b is ERR:
            return ERR
        if a == b:
            return a
        if T.is_untyped(a) and T.is_untyped(b):
            return T.UFLOAT if T.UFLOAT in (a, b) else T.UINT
        if T.is_untyped(a) and self.accepts_untyped(a, b):
            self.settle(ea, b)
            self.check_fits(ea, b)
            return b
        if T.is_untyped(b) and self.accepts_untyped(b, a):
            self.settle(eb, a)
            self.check_fits(eb, a)
            return a
        if T.widens(a, b):
            return b
        if T.widens(b, a):
            return a
        if T.is_num(a) and T.is_num(b):
            return self.err(f'cannot mix `{a}` and `{b}`', node,
                            help=f'convert one side with `as`, e.g. `x as {b if not T.is_untyped(b) else a}`')
        return self.err(f'expected `{a}`, found `{b}`', node)

    def accepts_untyped(self, u, t):
        if u == T.UINT:
            return T.is_num(t)
        if u == T.UFLOAT:
            return T.is_float(t)
        return False

    def check_fits(self, e, t):
        v = self.const_eval(e)
        if isinstance(v, int) and not isinstance(v, bool) and T.is_int(t) and not T.fits(v, t):
            self.err(f'{v} does not fit in `{t}`', e)

    def coerce(self, e, t, expect):
        if t is ERR or expect is ERR or expect is None:
            return
        if t == expect:
            return
        if T.is_untyped(t) and self.accepts_untyped(t, expect):
            self.check_fits(e, expect)
            self.settle(e, expect)
            return
        if T.widens(t, expect):
            return
        if t == T.UFLOAT and T.is_int(expect):
            self.err(f'expected `{expect}`, found a decimal number', e)
            return
        help = None
        if T.is_num(t) and T.is_num(expect):
            help = f'convert with `as {expect}`'
        elif T.is_num(t) and expect == T.DURATION:
            help = 'give it a unit, e.g. `100ms`'
        elif T.is_int(t) and expect == T.BOOL:
            help = 'compare it explicitly, e.g. `x != 0`'
        self.err(f'expected `{expect}`, found `{t}`', e, help=help)

    def settle(self, e, t):
        """Give every untyped literal inside e the concrete type t."""
        if e is None or t is None or t is ERR:
            return
        if not T.is_untyped(getattr(e, 'ty', None) or T.VOID):
            return
        e.ty = t
        if isinstance(e, N.Binary) and e.op not in ('==', '!=', '<', '<=', '>', '>=', 'and', 'or'):
            self.settle(e.left, t)
            if e.op in ('<<', '>>'):
                self.settle(e.right, T.I32)
            else:
                self.settle(e.right, t)
        elif isinstance(e, N.Unary):
            self.settle(e.operand, t)
        elif isinstance(e, N.IfExpr):
            self.settle(e.then, t)
            self.settle(e.other, t)

    def check_printable(self, e, t, fmt):
        if t is ERR:
            return
        ok = T.is_num(t) or t in (T.BOOL, T.STR, T.DURATION, T.FREQUENCY) or isinstance(t, T.EnumTy)
        if not ok:
            self.err(f'cannot print `{t}`', e, help='print its fields one by one')
            return
        if fmt:
            if fmt.startswith('.') and fmt[1:].isdigit():
                if not T.is_float(t):
                    self.err(f'`:{fmt}` sets decimals, which needs a float', e)
            elif fmt in ('x', 'X', 'b'):
                if not T.is_int(t):
                    self.err(f'`:{fmt}` needs an integer', e)
            else:
                self.err(f'unknown format `:{fmt}`', e, help='formats: `.2` (decimals), `x`, `X`, `b`')

    # ── calls and members ──────────────────────────────────────────────

    def call(self, e, expect):
        callee = e.callee
        if isinstance(callee, N.Name) or self.is_qualified(callee):
            s = self.static_ref(callee)
            if s is ERROR_SYM:
                for a in e.args:
                    self.expr(a.value, None)
                return ERR
            if s is None:
                if callee.name in ('print', 'println'):
                    return self.print_call(e)
                if callee.name in ('millis', 'micros', 'uptime'):
                    self.arity(e, 0)
                    e.res, e.target = 'builtin', callee.name
                    return T.DURATION if callee.name == 'uptime' else T.U32
                if callee.name in ('abs', 'min', 'max', 'clamp'):
                    return self.generic_num(e, callee.name, {'abs': 1, 'min': 2, 'max': 2, 'clamp': 3}[callee.name])
                if callee.name in PRELUDE_FLOAT_1 or callee.name == 'pow':
                    return self.float_fn(e, callee.name, 2 if callee.name == 'pow' else 1, prelude=True)
                hint = None
                if callee.name in ('digitalWrite', 'digitalRead', 'analogRead', 'pinMode'):
                    hint = 'declare a `pin` and use its methods'
                elif callee.name in ('delay', 'sleep'):
                    hint = 'use `wait 100ms` inside a `task`'
                elif callee.name in MATH_1 | MATH_2:
                    hint = f'add `use c.math` and call `math.{callee.name}`'
                return self.err(f'unknown function `{callee.name}`', callee, help=hint)
            callee.ref = s
            if s.kind == 'fn':
                e.res, e.target = 'fn', s.decl
                if self.ctx is not None:
                    self.ctx.calls.append((s, e))
                    if isinstance(s.decl, N.ExternFn) and s.decl.blocking:
                        self.ctx.blocking.append((s.decl, e))
                return self.bind_args(e, s.decl.params, s.decl, s.decl.ret_ty)
            if s.kind == 'type' and isinstance(s.ty, T.StructTy):
                return self.ctor(e, s.ty)
            if s.kind == 'type' and isinstance(s.ty, T.ClassTy):
                return self.class_ctor(e, s.ty)
            return self.err(f'`{s.name}` is not a function', callee)
        if isinstance(callee, N.Member):
            obj = callee.obj
            if isinstance(obj, N.Name) or self.is_qualified(obj):
                s = self.static_ref(obj)
                if s is ERROR_SYM:
                    return ERR
                if s is not None:
                    if s.kind == 'pin':
                        return self.pin_call(e, s, callee)
                    if s.kind == 'wifi':
                        if self.ctx is not None:
                            self.ctx.touches.append((s, callee))
                        methods = WIFI_METHODS[s.decl.mode]
                        if callee.name not in methods:
                            if callee.name in WIFI_PROPS[s.decl.mode]:
                                return self.err(f'`{callee.name}` is a property; drop the `()`', callee)
                            return self.err(f'`{s.name}` has no method `{callee.name}`', callee,
                                            help=('methods: ' + ', '.join(methods)) if methods else None)
                        self.arity(e, 0)
                        e.res, e.target = 'wifi', (s.decl, callee.name)
                        return T.VOID
                    if s.kind == 'timer':
                        if self.ctx is not None:
                            self.ctx.touches.append((s, callee))
                        if callee.name not in ('start', 'stop'):
                            return self.err(f'timers have `start()` and `stop()`, not `{callee.name}`',
                                            callee)
                        self.arity(e, 0)
                        e.res, e.target = 'timer', (s.decl, callee.name)
                        return T.VOID
                    if s.kind == 'shared':
                        return self.shared_call(e, s, callee)
                    if s.kind == 'module':
                        return self.math_call(e, callee)
                    if s.kind == 'type' and isinstance(s.ty, T.StructTy):
                        fn = s.ty.methods.get(callee.name)
                        if fn is None:
                            return self.err(f'`{s.name}` has no function `{callee.name}`', callee)
                        if fn.self_kind:
                            return self.err(f'`{callee.name}` is a method; call it on a value', callee)
                        e.res, e.target = 'static', fn
                        return self.bind_args(e, fn.params, fn, fn.ret_ty)
            ot = self.expr(obj, None)
            if ot is ERR:
                return ERR
            if isinstance(ot, T.StructTy):
                fn = ot.methods.get(callee.name)
                if fn is None:
                    if callee.name in ot.fields:
                        return self.err(f'`{callee.name}` is a field, not a method', callee)
                    return self.err(f'`{ot.name}` has no method `{callee.name}`', callee)
                if not fn.self_kind:
                    return self.err(f'`{callee.name}` has no `self`; call it as `{ot.name}.{callee.name}()`',
                                    callee)
                if fn.self_kind == 'mut self':
                    self.check_place_root(obj, f'`{callee.name}` changes the value')
                e.res, e.target = 'method', fn
                return self.bind_args(e, fn.params, fn, fn.ret_ty)
            if isinstance(ot, T.ClassTy):
                fn = ot.methods.get(callee.name)
                if fn is None:
                    return self.err(f'`{ot.name}` has no method `{callee.name}` in its binding', callee,
                                    help='methods: ' + (', '.join(ot.methods) or 'none'))
                if fn.self_kind == 'mut self':
                    self.check_place_root(obj, f'`{callee.name}` changes the object')
                e.res, e.target = 'ext_method', fn
                if fn.blocking and self.ctx is not None:
                    self.ctx.blocking.append((fn, e))
                return self.bind_args(e, fn.params, fn, fn.ret_ty)
            if ot == T.DURATION and callee.name in ('as_ms', 'as_us', 'as_s'):
                self.arity(e, 0)
                e.res, e.target = 'duration', callee.name
                return T.F32 if callee.name == 'as_s' else T.I64
            if isinstance(ot, T.ArrayTy) and callee.name == 'len':
                self.arity(e, 0)
                e.res, e.target = 'len', ot.size
                return T.USIZE
            return self.err(f'`{ot}` has no method `{callee.name}`', callee)
        return self.err('this cannot be called', callee)

    def check_place_root(self, e, why):
        root = e
        while isinstance(root, (N.Member, N.Index)):
            root = root.obj
        if isinstance(root, N.SelfRef):
            if not self.ctx.self_mut:
                self.err(f'{why}; this method needs `mut self`', e)
            return
        if isinstance(root, N.Name) and root.ref is not None:
            if not root.ref.mutable:
                self.err(f'{why}, but `{root.ref.name}` is immutable', e,
                         help=f'declare it with `var`' if root.ref.kind in ('let', 'local') else None)
            return
        self.err(f'{why}; call it on a variable', e)

    def arity(self, e, n):
        if len(e.args) != n:
            self.err(f'expected {n} argument{"s" if n != 1 else ""}, found {len(e.args)}', e)
        for a in e.args:
            if a.name is not None:
                self.err('this function takes no named arguments', a)
        e.ordered = [a.value for a in e.args]

    def print_call(self, e):
        e.res, e.target = 'print', e.callee.name
        self.info.uses_serial = True
        for a in e.args:
            if a.name is not None:
                self.err('`print` takes no named arguments', a)
            t = self.expr(a.value, None)
            self.settle(a.value, T.default_of(t))
            if t == T.VOID:
                self.err('this has no value to print', a.value)
            else:
                self.check_printable(a.value, t, None)
        e.ordered = [a.value for a in e.args]
        return T.VOID

    def generic_num(self, e, name, n):
        self.arity(e, n)
        if len(e.args) != n:
            for a in e.args:
                self.expr(a.value, None)
            return ERR
        types = [self.expr(a.value, None) for a in e.args]
        t = types[0]
        for a, ti in zip(e.args[1:], types[1:]):
            t = self.unify(t, ti, e.args[0].value, a.value, e)
        if t is ERR:
            return ERR
        if not (T.is_num(t) or t == T.DURATION):
            return self.err(f'`{name}` needs numbers, found `{t}`', e)
        t = T.default_of(t)
        for a in e.args:
            self.settle(a.value, t)
            self.coerce(a.value, a.value.ty, t)
        if name == 'abs' and T.is_int(t) and not T.INT_INFO[t.name][0]:
            self.err(f'`abs` of unsigned `{t}` does nothing', e)
        e.res, e.target = 'builtin', name
        return t

    def float_fn(self, e, name, n, prelude=False):
        self.arity(e, n)
        types = [self.expr(a.value, None) for a in e.args]
        if len(e.args) != n or ERR in types:
            return ERR
        t = None
        for a, ti in zip(e.args, types):
            if T.is_untyped(ti):
                continue
            if not T.is_float(ti):
                return self.err(f'`{name}` needs a float, found `{ti}`', a.value,
                                help=f'convert it with `as f32`' if T.is_num(ti) else None)
            t = ti if t is None else self.unify(t, ti, e.args[0].value, a.value, e)
        t = t or T.F32
        for a in e.args:
            self.settle(a.value, t)
        e.res, e.target = ('builtin' if prelude else 'math'), name
        return t

    def math_call(self, e, callee):
        name = callee.name
        if name in MATH_1:
            return self.float_fn(e, name, 1)
        if name in MATH_2:
            return self.float_fn(e, name, 2)
        if name in MATH_N:
            return self.float_fn(e, name, MATH_N[name])
        if name in ('abs', 'min', 'max', 'clamp'):
            return self.generic_num(e, name, {'abs': 1, 'min': 2, 'max': 2, 'clamp': 3}[name])
        return self.err(f'`math` has no function `{name}`', callee)

    def pin_call(self, e, s, callee):
        methods = PIN_METHODS[s.ty.kind]
        if callee.name not in methods:
            return self.err(f'`{s.ty.kind}` pins have no method `{callee.name}`', callee,
                            help='methods: ' + ', '.join(f'{m}()' for m in methods))
        params, ret = methods[callee.name]
        if len(e.args) != len(params):
            self.err(f'`{callee.name}` takes {len(params)} argument(s)', e)
        for a, pt in zip(e.args, params):
            t = self.expr(a.value, pt)
            self.coerce(a.value, t, pt)
        e.ordered = [a.value for a in e.args]
        e.res, e.target = 'pin', (s.decl, callee.name)
        return ret

    def shared_call(self, e, s, callee):
        ty = s.ty
        if callee.name == 'get':
            self.arity(e, 0)
            e.res, e.target = 'shared', (s.decl, 'get')
            return ty
        if callee.name in ('set', 'add'):
            if callee.name == 'add' and not T.is_int(ty):
                return self.err('`add` works on integer shared values', callee)
            if len(e.args) != 1:
                return self.err(f'`{callee.name}` takes one argument', e)
            t = self.expr(e.args[0].value, ty)
            self.coerce(e.args[0].value, t, ty)
            e.ordered = [e.args[0].value]
            e.res, e.target = 'shared', (s.decl, callee.name)
            return T.VOID if callee.name == 'set' else ty
        return self.err(f'shared values have `get()`, `set(v)` and `add(n)`, not `{callee.name}`',
                        callee)

    def ctor(self, e, sty):
        e.res, e.target = 'ctor', sty
        given = {}
        positional = [a for a in e.args if a.name is None]
        named = [a for a in e.args if a.name is not None]
        names = list(sty.fields)
        if positional and named and e.args.index(named[0]) < len(positional) - 1:
            self.err('positional fields must come before named ones', e)
        for i, a in enumerate(positional):
            if i >= len(names):
                self.err(f'`{sty.name}` has only {len(names)} fields', a)
                continue
            given[names[i]] = a
        for a in named:
            if a.name not in sty.fields:
                self.err(f'`{sty.name}` has no field `{a.name}`', a)
                continue
            if a.name in given:
                self.err(f'field `{a.name}` given twice', a)
            given[a.name] = a
        ordered = []
        for fname, (fty, f) in sty.fields.items():
            if fname in given:
                v = given[fname].value
                t = self.expr(v, fty)
                if isinstance(v, N.StrLit) and self.has_interp(v):
                    self.err('formatted strings can only be printed for now', v)
                self.coerce(v, t, fty)
                ordered.append(v)
            elif f.default is not None:
                t = self.expr(f.default, fty)
                self.coerce(f.default, t, fty)
                ordered.append(f.default)
            else:
                self.err(f'missing field `{fname}` for `{sty.name}`', e,
                         help=f'add `{fname}: ...` or give the field a default in the struct')
                ordered.append(None)
        e.ordered = ordered
        return sty

    def bind_args(self, e, params, fn, ret):
        slots = [None] * len(params)
        index = {p.name: i for i, p in enumerate(params)}
        pos = 0
        seen_named = False
        for a in e.args:
            if a.name is None:
                if seen_named:
                    self.err('positional arguments must come before named ones', a)
                if pos >= len(params):
                    self.err(f'`{fn.name}` takes {len(params)} argument(s)', a)
                    self.expr(a.value, None)
                    continue
                slots[pos] = a
                pos += 1
            else:
                seen_named = True
                if a.name not in index:
                    self.err(f'`{fn.name}` has no parameter `{a.name}`', a)
                    self.expr(a.value, None)
                    continue
                if slots[index[a.name]] is not None:
                    self.err(f'argument `{a.name}` given twice', a)
                slots[index[a.name]] = a
        ordered = []
        for p, a in zip(params, slots):
            if a is None:
                if p.default is None:
                    self.err(f'missing argument `{p.name}` for `{fn.name}`', e)
                    ordered.append(None)
                else:
                    ordered.append(p.default)
                continue
            t = self.expr(a.value, p.ty)
            if isinstance(a.value, N.StrLit) and self.has_interp(a.value):
                self.err('formatted strings can only be printed for now', a.value)
            self.coerce(a.value, t, p.ty)
            if isinstance(p.ty, T.ClassTy) and not isinstance(a.value, (N.Name, N.Member)):
                self.err(f'pass a `{p.ty.name}` object by name', a.value)
            if p.mutable:
                self.check_place_root(a.value, f'`{p.name}` is a `mut` parameter')
                if not isinstance(a.value, (N.Name, N.Member, N.Index)):
                    self.err(f'`{p.name}` is a `mut` parameter; pass a variable', a.value)
            ordered.append(a.value)
        e.ordered = ordered
        return ret

    def member(self, e):
        if self.is_qualified(e):
            s = self.static_ref(e)
            if s is ERROR_SYM:
                return ERR
            t = self.use_sym(e, s)
            e.res, e.target = 'qualified', s
            return t
        obj = e.obj
        if isinstance(obj, N.Name) or self.is_qualified(obj):
            s = self.static_ref(obj)
            if s is ERROR_SYM:
                return ERR
            if s is not None:
                if s.kind == 'type' and isinstance(s.ty, T.EnumTy):
                    if e.name not in s.ty.variants:
                        return self.err(f'`{s.name}` has no variant `{e.name}`', e,
                                        help='variants: ' + ', '.join(s.ty.variants))
                    e.res, e.target = 'variant', e.name
                    return s.ty
                if s.kind == 'timer':
                    if self.ctx is not None:
                        self.ctx.touches.append((s, e))
                    if e.name != 'running':
                        return self.err(f'timers have `.running`, `.start()` and `.stop()`', e)
                    e.res, e.target = 'timer_running', s.decl
                    return T.BOOL
                if s.kind == 'module':
                    if e.name in MATH_CONSTS:
                        e.res, e.target = 'math_const', e.name
                        return T.UFLOAT
                    return self.err(f'`math.{e.name}` is a function; call it', e)
                if s.kind in ('pin', 'shared'):
                    return self.err(f'`{e.name}` is a method; add `()`', e)
                if s.kind == 'wifi':
                    if self.ctx is not None:
                        self.ctx.touches.append((s, e))
                    props = WIFI_PROPS[s.decl.mode]
                    if e.name not in props:
                        if e.name in WIFI_METHODS[s.decl.mode]:
                            return self.err(f'`{e.name}` is a method; add `()`', e)
                        return self.err(f'`{s.name}` has no property `{e.name}`', e,
                                        help='properties: ' + ', '.join(props))
                    e.res, e.target = 'wifi_prop', (s.decl, e.name)
                    return {'connected': T.BOOL, 'state': self.wifi_state_sym().ty, 'ip': T.STR,
                            'rssi': T.I32, 'mac': T.STR, 'clients': T.U8}[e.name]
        ot = self.expr(obj, None)
        if ot is ERR:
            return ERR
        if isinstance(ot, T.StructTy):
            if e.name in ot.fields:
                e.res = 'field'
                return ot.fields[e.name][0]
            if e.name in ot.methods:
                return self.err(f'`{e.name}` is a method; call it with `()`', e)
            return self.err(f'`{ot.name}` has no field `{e.name}`', e)
        if isinstance(ot, T.ArrayTy) and e.name == 'len':
            return self.err('`len` is a method; write `.len()`', e)
        return self.err(f'`{ot}` has no field `{e.name}`', e)

    # ── pass 3: which core owns what ───────────────────────────────────

    def check_ownership(self):
        closure = {}

        def uses(fsym, stack):
            if fsym in closure:
                return closure[fsym]
            if fsym in stack:
                return {}
            ctx = self.fn_ctx.get(fsym)
            result = {}
            if ctx is not None:
                stack.add(fsym)
                for g, node in ctx.touches:
                    result.setdefault(g, (node, None))
                for callee, node in ctx.calls:
                    for g, (n2, via) in uses(callee, stack).items():
                        result.setdefault(g, (node, via or callee.name))
                stack.discard(fsym)
            closure[fsym] = result
            return result

        bclosure = {}

        def blocks(fsym, stack):
            """Blocking binding calls reachable from a function: [(ExternFn, via)]."""
            if fsym in bclosure:
                return bclosure[fsym]
            if fsym in stack:
                return []
            ctx = self.fn_ctx.get(fsym)
            result = []
            if ctx is not None:
                stack.add(fsym)
                result = [(fn, None) for fn, _ in ctx.blocking]
                for callee, _ in ctx.calls:
                    result += [(fn, via or callee.name) for fn, via in blocks(callee, stack)]
                stack.discard(fsym)
            bclosure[fsym] = result
            return result

        def too_slow(fn):
            return fn.max_us is None or fn.max_us > BLOCK_BUDGET_US

        def block_help(fn):
            if fn.max_us is None:
                return ('call it from `on boot`, or if it is always short, declare it '
                        '`blocking(max: 2ms)` in the binding (running it on a worker with '
                        '`offload` is planned)')
            return 'call it from `on boot` (running it on a worker with `offload` is planned)'

        def block_what(fn):
            if fn.max_us is None:
                return 'can block for an unknown time'
            return f'can block for up to {fn.max_us // 1000}ms'

        def core_name(c):
            label = self.core_labels.get(c)
            return f'core {c} ({label})' if label else f'core {c}'

        for ctx in self.contexts:
            if not ctx.boot:
                for fn, node in ctx.blocking:
                    if too_slow(fn):
                        self.err(f'`{fn.name}` {block_what(fn)}, which would stall core {ctx.core}',
                                 node, help=block_help(fn))
                for fsym, node in ctx.calls:
                    for fn, via in blocks(fsym, set()):
                        if too_slow(fn):
                            through = f' (through `{via}`)' if via else ''
                            self.err(f'`{fsym.name}` calls `{fn.name}`{through}, which '
                                     f'{block_what(fn)} and would stall core {ctx.core}', node,
                                     help=block_help(fn))
                            break
            c = ctx.core
            seen = set()
            for g, node in ctx.touches:
                if g.core is not None and g.core != c and g not in seen:
                    seen.add(g)
                    help = {'timer': 'control a timer from the core it runs on',
                            'wifi': 'use WiFi from the core it is declared on; pass what the other '
                                    'core needs through a `shared var`'}.get(
                        g.kind, 'use a `shared var` for values both cores need')
                    self.err(f'`{g.name}` belongs to {core_name(g.core)}, but {ctx.label} runs on '
                             f'{core_name(c)}', node, help=help)
            reported = set()
            for fsym, node in ctx.calls:
                for g, (_, via) in uses(fsym, set()).items():
                    if g.core is not None and g.core != c and (fsym, g) not in reported:
                        reported.add((fsym, g))
                        through = f' (through `{via}`)' if via else ''
                        self.err(f'`{fsym.name}` uses `{g.name}`{through}, which belongs to '
                                 f'{core_name(g.core)}, but {ctx.label} runs on {core_name(c)}', node,
                                 help='use a `shared var` for values both cores need')


# ── helpers ────────────────────────────────────────────────────────────

def walk_expr(e):
    if e is None:
        return
    yield e
    if isinstance(e, N.Unary):
        yield from walk_expr(e.operand)
    elif isinstance(e, N.Binary):
        yield from walk_expr(e.left)
        yield from walk_expr(e.right)
    elif isinstance(e, N.Call):
        yield from walk_expr(e.callee)
        for a in e.args:
            yield from walk_expr(a.value)
    elif isinstance(e, N.Member):
        yield from walk_expr(e.obj)
    elif isinstance(e, N.Index):
        yield from walk_expr(e.obj)
        yield from walk_expr(e.index)
    elif isinstance(e, N.Cast):
        yield from walk_expr(e.expr)
    elif isinstance(e, N.IfExpr):
        yield from walk_expr(e.cond)
        yield from walk_expr(e.then)
        yield from walk_expr(e.other)
    elif isinstance(e, N.ArrayLit):
        for i in e.items:
            yield from walk_expr(i)
    elif isinstance(e, N.StrLit):
        for p in e.parts:
            if isinstance(p, N.Interpolation):
                yield from walk_expr(p.expr)


def child_blocks(s):
    if isinstance(s, N.If):
        for _, b in s.branches:
            yield b
        if s.else_body is not None:
            yield s.else_body
    elif isinstance(s, (N.While, N.For, N.Loop)):
        yield s.body
    elif isinstance(s, N.Match):
        for arm in s.arms:
            yield arm.body


def walk_stmts(stmts):
    for s in stmts:
        yield s
        for b in child_blocks(s):
            yield from walk_stmts(b)


def has_exit(body):
    """Does a `loop` body contain a break (for this loop) or a return?"""
    for s in body:
        if isinstance(s, (N.Break, N.Return)):
            return True
        if isinstance(s, (N.While, N.For, N.Loop)):
            if any(isinstance(x, N.Return) for x in walk_stmts(s.body)):
                return True
            continue
        for b in child_blocks(s):
            if has_exit(b):
                return True
    return False


def always_returns(stmts):
    for s in stmts:
        if isinstance(s, N.Return):
            return True
        if isinstance(s, N.If) and s.else_body is not None:
            if all(always_returns(b) for _, b in s.branches) and always_returns(s.else_body):
                return True
        if isinstance(s, N.Match) and getattr(s, 'exhaustive', False):
            if all(always_returns(a.body) for a in s.arms):
                return True
        if isinstance(s, N.Loop) and not any(isinstance(x, N.Break) for x in walk_stmts(s.body)):
            return True
    return False


def check(prog, source=None):
    return Checker(prog, source).check()
