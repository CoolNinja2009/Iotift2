"""Iotift 2 code generator: checked AST -> one Arduino-ESP32 C++ file.

Layout of the output:
  includes, runtime (namespace iot), enums, structs, constants, pins,
  globals, function prototypes, function and method bodies, timers and
  event handlers, task state machines, one executor per core, setup()/loop().

User names get a trailing underscore (`count` -> `count_`) so they can never
clash with Arduino macros or C++ keywords; everything the compiler invents
starts with `iot_`.
"""

import os

from . import nodes as N
from . import typesys as T
from .runtime import RUNTIME

DEFAULT_CORE = 1


class CodeGenError(Exception):
    pass


def u(name):
    return name + '_'


def bare(s):
    """*s* without one pair of parentheses around the whole of it. `if ((a == b))` makes clang
    warn (-Wparentheses-equality), so conditions are emitted through this."""
    if not (s.startswith('(') and s.endswith(')')):
        return s
    depth, i = 0, 0
    while i < len(s):
        ch = s[i]
        if ch in '"\'':                # skip a C++ string or char literal
            i += 1
            while i < len(s) and s[i] != ch:
                i += 2 if s[i] == '\\' else 1
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0 and i != len(s) - 1:
                return s                # e.g. `(a) && (b)`: the first pair closes early
        i += 1
    return s[1:-1]


def c_str(s):
    out = []
    for ch in s:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\0':
            out.append('\\0')
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append('\\x%02x""' % ord(ch))
        else:
            out.append(ch)
    return '"' + ''.join(out) + '"'


def ctype(t):
    if isinstance(t, T.Prim):
        return T.CPP[t.name]
    if isinstance(t, (T.StructTy, T.EnumTy, T.ClassTy)):
        return t.cname
    if isinstance(t, T.ArrayTy):
        return f'iot::Arr<{ctype(t.elem)}, {t.size}>'
    if isinstance(t, T.Untyped):
        return ctype(T.default_of(t))
    raise CodeGenError(f'no C++ type for {t}')


def by_ref(t):
    return isinstance(t, (T.StructTy, T.ArrayTy))


def float_lit(v, t):
    r = repr(float(v))
    if r in ('inf', '-inf', 'nan'):
        raise CodeGenError(f'cannot emit float literal {r}')
    if 'e' not in r and '.' not in r:
        r += '.0'
    return r + ('f' if t == T.F32 else '')


def int_lit(v, t):
    if T.is_float(t):
        return float_lit(float(v), t)
    if t == T.DURATION:
        return f'INT64_C({v})'
    name = t.name if isinstance(t, T.Prim) else 'i32'
    if name == 'i64':
        return f'INT64_C({v})'
    if name == 'u64':
        return f'UINT64_C({v})'
    if name in ('u32', 'usize', 'Frequency'):
        return f'{v}u'
    if name == 'i32' and v > 2147483647:
        return f'INT64_C({v})'
    return str(v)


class Emitter:
    def __init__(self):
        self.lines = []
        self.level = 0

    def __call__(self, text=''):
        if text:
            self.lines.append('  ' * self.level + text)
        else:
            self.lines.append('')

    def raw(self, text):
        self.lines.append(text)

    def indent(self):
        self.level += 1

    def dedent(self):
        self.level -= 1


class CodeGen:
    def __init__(self, modules, info, checker, out_name='main.cpp', line_directives=True):
        if isinstance(modules, N.Program):
            prog = modules
        else:
            prog = modules[-1].prog
        self.prog = prog
        self.info = info
        self.checker = checker
        self.src_name = os.path.basename(prog.file) if prog.file else 'main.iot'
        self.entry_name = self.src_name
        self.out_name = out_name
        self.line_directives = line_directives
        self.e = Emitter()
        self.task = None          # TaskDecl being generated
        self.frame = None         # frame member list for that task
        self.tmp_n = 0
        self.state_n = 0
        self.cur_line = 0

    # ── helpers ────────────────────────────────────────────────────────

    def fail(self, msg, node):
        raise CodeGenError(f'{self.src_name}:{getattr(node, "line", 0)}: {msg}')

    def in_file(self, item):
        self.src_name = getattr(item, 'file', None) or self.entry_name

    def at_line(self, node):
        if self.line_directives and getattr(node, 'line', 0):
            self.e.raw(f'#line {node.line} "{self.src_name}"')

    def reset_lines(self):
        if self.line_directives:
            self.e.raw(f'#line {len(self.e.lines) + 2} "{self.out_name}"')

    def tmp(self, t, hint='tmp'):
        """A temporary: a frame member inside tasks, otherwise a local."""
        self.tmp_n += 1
        name = f'iot_{hint}{self.tmp_n}'
        if self.task is not None:
            self.frame.append((ctype(t), name))
            return f'f.{name}', None
        return name, f'{ctype(t)} {name}'

    # ── entry ──────────────────────────────────────────────────────────

    def generate(self):
        e = self.e
        info = self.info
        e(f'// Generated by the Iotift 2 compiler from {self.entry_name}. Do not edit.')
        e(f'// Target: {info.chip.name} (Arduino-ESP32 core 2.x or 3.x)')
        e('#include <Arduino.h>')
        e('#include <stdint.h>')
        e('#include <string.h>')
        e('#include <math.h>')
        if any(g.kind == 'shared' for g in info.globals) or info.wifi:
            e('#include <atomic>')
        if info.wifi:
            e('#include <WiFi.h>')
        e('#if !defined(IOTIFT_SIM)')
        e('#include <esp_timer.h>')
        e('#endif')
        for h in info.includes:
            e(f'#include <{h}>')
        for line in RUNTIME.strip('\n').split('\n'):
            e.raw(line)
        e()
        self.enums()
        self.structs()
        self.consts_and_pins()
        self.globals()
        self.prototypes()
        for fn in info.fns:
            self.function(fn)
        for fn in info.methods:
            self.function(fn)
        self.wifi_decls()
        self.handlers()
        self.wifi()
        for task in info.tasks:
            self.task_fn(task)
        self.executors()
        self.setup_loop()
        return '\n'.join(e.lines) + '\n'

    # ── declarations ───────────────────────────────────────────────────

    def enums(self):
        e = self.e
        for en in self.info.enums:
            e(f'enum class {en.cname} : uint8_t {{ ' + ', '.join(u(v) for v in en.variants) + ' };')
            e(f'static inline const char *iot_name({en.cname} v) {{')
            e.indent()
            e('switch (v) {')
            for v in en.variants:
                e(f'  case {en.cname}::{u(v)}: return {c_str(v)};')
            e('}')
            e('return "?";')
            e.dedent()
            e('}')
            e()

    def structs(self):
        e = self.e
        for st in self.info.structs:
            e(f'struct {st.cname};')
        for st in self.info.structs:
            e(f'struct {st.cname} {{')
            e.indent()
            for fname, (fty, f) in st.fields.items():
                init = f' = {self.expr(f.default)}' if f.default is not None else '{}'
                e(f'{ctype(fty)} {u(fname)}{init};')
            for fn in st.methods.values():
                e(self.signature(fn, inside=True) + ';')
            e.dedent()
            e('};')
            e()

    def consts_and_pins(self):
        e = self.e
        for g in self.info.globals:
            if g.kind == 'const' and not T.is_untyped(g.ty):
                e(f'static constexpr {ctype(g.ty)} {g.sym.cname} = {self.expr(g.init)};')
        for p in self.info.pins:
            e(f'static constexpr int {p.cname} = {p.gpio};  // {p.kind} pin')
        e()

    def globals(self):
        e = self.e
        for g in self.info.globals:
            if g.kind == 'const':
                continue
            self.in_file(g)
            self.at_line(g)
            n = g.sym.cname
            if isinstance(g.ty, T.ClassTy):
                args = self.args(g.init) if g.init is not None else ''
                e(f'static {ctype(g.ty)} {n}' + (f'({args});' if args else ';'))
                continue
            init = self.expr(g.init) if g.init is not None else None
            if g.kind == 'shared':
                e(f'static std::atomic<{ctype(g.ty)}> {n}{{{init if init else ""}}};')
            elif g.kind == 'let':
                e(f'static const {ctype(g.ty)} {n} = {init};')
            else:
                e(f'static {ctype(g.ty)} {n}' + (f' = {init};' if init else '{};'))
        self.reset_lines()
        for t in self.info.timers:
            if t.label:
                e(f'static iot::Timer {self.timer_name(t)};')
        e()

    def timer_name(self, t):
        return f'iot_timer_{t.prefix}{t.label}'

    def param_decl(self, p):
        t = ctype(p.ty)
        if p.mutable or isinstance(p.ty, T.ClassTy):
            return f'{t} &{u(p.name)}'
        if by_ref(p.ty):
            return f'const {t} &{u(p.name)}'
        return f'{t} {u(p.name)}'

    def signature(self, fn, inside=False):
        ret = ctype(fn.ret_ty)
        params = ', '.join(self.param_decl(p) for p in fn.params)
        if isinstance(fn.owner, T.StructTy):
            if inside:
                prefix = '' if fn.self_kind else 'static '
                const = ' const' if fn.self_kind == 'self' else ''
                return f'{prefix}{ret} {u(fn.name)}({params}){const}'
            const = ' const' if fn.self_kind == 'self' else ''
            return f'{ret} {fn.owner.cname}::{u(fn.name)}({params}){const}'
        return f'static {ret} {fn.cname}({params})'

    def prototypes(self):
        for fn in self.info.fns:
            self.e(self.signature(fn) + ';')
        self.e()

    def function(self, fn):
        self.in_file(fn)
        e = self.e
        e(self.signature(fn) + ' {')
        e.indent()
        self.block(fn.body)
        e.dedent()
        e('}')
        self.reset_lines()
        e()

    # ── handlers and tasks ─────────────────────────────────────────────

    def handlers(self):
        e = self.e
        info = self.info
        for i, t in enumerate(info.timers):
            t.cname = f'iot_every_{t.prefix}{t.label}' if t.label else f'iot_every_{i}'
            t.tname = self.timer_name(t) if t.label else f'iot_timer_{i}'
            if not t.label:
                e(f'static iot::Timer {t.tname};')
            self.in_file(t)
            self.handler_fn(t.cname, t.body, f'every {t.period_us // 1000}ms')
        for i, a in enumerate(info.afters):
            a.cname = f'iot_after_{i}'
            e(f'static iot::Once iot_once_{i};')
            self.in_file(a)
            self.handler_fn(a.cname, a.body, f'after {a.delay_us // 1000}ms')
        for i, ev in enumerate(info.events):
            ev.cname = f'iot_on_{ev.pin.name}_{ev.event}_{i}'
            ev.dname = f'iot_db_{i}'
            e(f'static iot::Debounce {ev.dname};')
            self.in_file(ev)
            self.handler_fn(ev.cname, ev.body, f'on {ev.pin.name}.{ev.event}')
        for i, ev in enumerate(info.wifi_events):
            ev.cname = f'{ev.wifi.cname}_on_{ev.event}_{i}'
            self.in_file(ev)
            self.handler_fn(ev.cname, ev.body, f'on {ev.wifi.name}.{ev.event}')
        for kind in ('boot', 'ready'):
            for i, ev in enumerate(info.system[kind]):
                ev.cname = f'iot_on_{kind}_{i}'
                self.in_file(ev)
                self.handler_fn(ev.cname, ev.body, f'on {kind}')

    def wifi_decls(self):
        """The state behind WiFi properties, declared before the handlers that read it."""
        e = self.e
        for w in self.info.wifi:
            n = w.cname
            e(f'static char {n}_mac[18] = "";')
            e(f'static char {n}_ip[16] = "";')
            if w.mode == 'sta':
                first = w.retry[1] if w.retry[0] != 'none' else 0
                e(f'static iot_WifiState {n}_state = iot_WifiState::idle_;')
                e(f'static uint32_t {n}_until = 0;   // millis() when the attempt or the pause ends')
                e(f'static uint32_t {n}_pause = {first}u;   // next pause between attempts')
                e(f'static void {n}_connect();')
                e(f'static void {n}_disconnect();')
            e()

    def wifi(self):
        """WiFi driver events arrive on the system event task. The callback only
        counts them; the owning core's executor picks them up, runs the
        connect/retry state machine and calls the user's handlers."""
        e = self.e
        info = self.info
        if not info.wifi:
            return
        e('// -- WiFi --')
        e('static std::atomic<uint32_t> iot_wifi_got_ip{0}, iot_wifi_lost{0}, iot_wifi_joined{0}, iot_wifi_left{0};')
        e('static void iot_wifi_event(arduino_event_id_t ev, arduino_event_info_t) {')
        e('  switch (ev) {')
        e('    case ARDUINO_EVENT_WIFI_STA_GOT_IP: iot_wifi_got_ip++; break;')
        e('    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED: iot_wifi_lost++; break;')
        e('    case ARDUINO_EVENT_WIFI_AP_STACONNECTED: iot_wifi_joined++; break;')
        e('    case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED: iot_wifi_left++; break;')
        e('    default: break;')
        e('  }')
        e('}')
        e()
        for w in info.wifi:
            if w.mode == 'ap':
                continue
            n = w.cname
            handlers = {k: handlers_of(info, w, k) for k in ('connected', 'lost', 'failed')}
            ssid = c_str(w.ssid or '')
            pw = c_str(w.password) if w.password is not None else 'nullptr'
            first = w.retry[1] if w.retry[0] != 'none' else 0
            e(f'static void {n}_begin(uint32_t now) {{')
            e(f'  WiFi.begin({ssid}, {pw});')
            e(f'  {n}_state = iot_WifiState::connecting_;')
            e(f'  {n}_until = now + {w.timeout_ms}u;')
            e('}')
            e(f'static void {n}_connect() {{')
            e(f'  if ({n}_state != iot_WifiState::idle_ && {n}_state != iot_WifiState::failed_) return;')
            e(f'  {n}_pause = {first}u;')
            e(f'  {n}_begin(millis());')
            e('}')
            e(f'static void {n}_disconnect() {{')
            e(f'  if ({n}_state == iot_WifiState::idle_) return;')
            e(f'  {n}_state = iot_WifiState::idle_;')
            e(f'  {n}_ip[0] = 0;')
            e('  WiFi.disconnect();')
            e('}')
            e(f'// An attempt failed or the link dropped: {self.retry_text(w.retry)}.')
            e(f'static void {n}_retry(uint32_t now) {{')
            e(f'  {n}_ip[0] = 0;')
            if w.retry[0] == 'none':
                e(f'  {n}_state = iot_WifiState::failed_;')
                for h in handlers['failed']:
                    e(f'  {h}();')
            else:
                e(f'  {n}_state = iot_WifiState::retrying_;')
                e(f'  {n}_until = now + {n}_pause;')
                if w.retry[0] == 'backoff':
                    e(f'  {n}_pause = {n}_pause >= {w.retry[2]}u / 2 ? {w.retry[2]}u : {n}_pause * 2;')
            e('}')
            e(f'static void {n}_poll(uint32_t now) {{')
            e('  const uint32_t lost = iot_wifi_lost.exchange(0), got = iot_wifi_got_ip.exchange(0);')
            e(f'  if (lost && {n}_state == iot_WifiState::connected_) {{')
            e(f'    {n}_ip[0] = 0;')
            e(f'    {n}_state = iot_WifiState::retrying_;')
            for h in handlers['lost']:
                e(f'    {h}();')
            e(f'    if ({n}_state == iot_WifiState::retrying_) {{  // unless a handler disconnected')
            e(f'      {n}_pause = {first}u;')
            if w.retry[0] == 'none':
                e(f'      {n}_retry(now);')
            else:
                e(f'      {n}_begin(now);  // first try again right away')
            e('    }')
            e(f'  }} else if (lost && {n}_state == iot_WifiState::connecting_) {{')
            e(f'    {n}_retry(now);')
            e('  }')
            e(f'  if (got && {n}_state == iot_WifiState::connecting_) {{')
            e(f'    {n}_state = iot_WifiState::connected_;')
            e(f'    {n}_pause = {first}u;')
            e(f'    snprintf({n}_ip, sizeof {n}_ip, "%s", WiFi.localIP().toString().c_str());')
            for h in handlers['connected']:
                e(f'    {h}();')
            e('  }')
            e(f'  if ({n}_state == iot_WifiState::connecting_ && iot::due(now, {n}_until)) {{')
            e('    WiFi.disconnect();  // gave up on this attempt')
            e(f'    {n}_retry(now);')
            e(f'  }} else if ({n}_state == iot_WifiState::retrying_ && iot::due(now, {n}_until)) {{')
            e(f'    {n}_begin(now);')
            e('  }')
            e('}')
            e()
        for w in info.wifi:
            if w.mode != 'ap':
                continue
            n = w.cname
            e(f'static void {n}_poll(uint32_t) {{')
            e('  for (uint32_t k = iot_wifi_joined.exchange(0); k; k--) {')
            for h in handlers_of(info, w, 'client_joined'):
                e(f'    {h}();')
            e('  }')
            e('  for (uint32_t k = iot_wifi_left.exchange(0); k; k--) {')
            for h in handlers_of(info, w, 'client_left'):
                e(f'    {h}();')
            e('  }')
            e('}')
            e()
        e('static void iot_wifi_start() {')
        e.indent()
        e('WiFi.persistent(false);  // settings live in the program, not in flash')
        e('WiFi.onEvent(iot_wifi_event);')
        sta = next((w for w in info.wifi if w.mode == 'sta'), None)
        ap = next((w for w in info.wifi if w.mode == 'ap'), None)
        if sta is not None and sta.hostname:
            e(f'WiFi.setHostname({c_str(sta.hostname)});')
        mode = 'WIFI_AP_STA' if sta and ap else ('WIFI_STA' if sta else 'WIFI_AP')
        e(f'WiFi.mode({mode});')
        if sta is not None:
            e('WiFi.setAutoReconnect(false);  // reconnecting is done by the retry policy')
            if sta.power_save:
                ps = {'none': 'WIFI_PS_NONE', 'modem': 'WIFI_PS_MIN_MODEM', 'max': 'WIFI_PS_MAX_MODEM'}
                e(f'WiFi.setSleep({ps[sta.power_save]});')
            e(f'snprintf({sta.cname}_mac, sizeof {sta.cname}_mac, "%s", WiFi.macAddress().c_str());')
        if ap is not None:
            pw = c_str(ap.password) if ap.password is not None else 'nullptr'
            e(f'WiFi.softAP({c_str(ap.ssid or "")}, {pw}, {ap.channel}, 0, {ap.max_clients});')
            e(f'snprintf({ap.cname}_ip, sizeof {ap.cname}_ip, "%s", WiFi.softAPIP().toString().c_str());')
            e(f'snprintf({ap.cname}_mac, sizeof {ap.cname}_mac, "%s", WiFi.softAPmacAddress().c_str());')
        if sta is not None and sta.autoconnect:
            e(f'{sta.cname}_begin(millis());')
        e.dedent()
        e('}')
        e()

    @staticmethod
    def retry_text(retry):
        if retry[0] == 'none':
            return 'give up'
        if retry[0] == 'fixed':
            return f'try again every {retry[1]}ms'
        return f'try again after {retry[1]}ms, doubling up to {retry[2]}ms'

    def handler_fn(self, name, body, what):
        e = self.e
        e(f'// {what}')
        e(f'static void {name}() {{')
        e.indent()
        self.block(body)
        e.dedent()
        e('}')
        self.reset_lines()
        e()

    def task_fn(self, task):
        """A task becomes a resumable state machine: a frame struct holding
        its state, wake-up time and every local, and a step function that
        runs until the next `wait` and returns."""
        self.in_file(task)
        self.task = task
        self.frame = []
        self.state_n = 0
        for i, sym in enumerate(task.locals):
            sym.cname = f'f.{sym.name}_{i}'
            self.frame.append((ctype(sym.ty), f'{sym.name}_{i}'))
        body = Emitter()
        saved = self.e
        self.e = body
        self.e.level = 1
        self.block(task.body)
        self.e = saved
        e = self.e
        name = f'iot_task_{task.prefix}{task.name}'
        task.cname = name
        e(f'// task {task.name} (core {task.core})')
        e(f'struct {name}_frame {{')
        e.indent()
        e('int32_t st = 0;        // resume point; -1 when finished')
        e('uint32_t wake = 0;     // millis() at which to resume')
        for ct, n in self.frame:
            e(f'{ct} {n}{{}};')
        e.dedent()
        e('};')
        e(f'static {name}_frame {name};')
        e(f'static void {name}_step() {{')
        e.indent()
        e(f'auto &f = {name};')
        e('switch (f.st) {')
        e('case 0:;')
        for line in body.lines:
            e.raw(line if line.startswith('#line') else '  ' * 1 + line)
        e('  f.st = -1;')
        e('  return;')
        e('default:')
        e('  return;')
        e('}')
        e.dedent()
        e('}')
        self.reset_lines()
        e()
        self.task = None
        self.frame = None

    def executors(self):
        e = self.e
        info = self.info
        cores = sorted(info.cores_used | {DEFAULT_CORE})
        for c in cores:
            e(f'static void iot_poll_core{c}() {{')
            e.indent()
            e('const uint32_t now = millis();')
            for w in info.wifi:
                if w.core == c:
                    e(f'{w.cname}_poll(now);')
            for t in info.timers:
                if t.core != c:
                    continue
                e(f'if ({t.tname}.running && iot::due(now, {t.tname}.next)) {{')
                e(f'  {t.tname}.next += {t.tname}.period;')
                e(f'  if (iot::due(now, {t.tname}.next)) {t.tname}.next = now + {t.tname}.period;  // fell behind: re-align')
                e(f'  {t.cname}();')
                e('}')
            for i, a in enumerate(info.afters):
                if a.core != c:
                    continue
                e(f'if (!iot_once_{i}.done && iot::due(now, iot_once_{i}.at)) {{')
                e(f'  iot_once_{i}.done = true;')
                e(f'  {a.cname}();')
                e('}')
            for ev in info.events:
                if ev.core != c:
                    continue
                p = ev.pin
                deb = p.opts['debounce'] // 1000
                cond = self.edge_condition(ev)
                e(f'if (iot::debounce({ev.dname}, digitalRead({p.cname}) == HIGH, now, {deb}u) && {cond}) {ev.cname}();')
            for task in info.tasks:
                if task.core != c:
                    continue
                n = task.cname
                e(f'if ({n}.st >= 0 && iot::due(now, {n}.wake)) {n}_step();')
            e.dedent()
            e('}')
            e()

    def edge_condition(self, ev):
        d = ev.dname
        pull = ev.pin.opts['pull']
        if ev.event == 'change':
            return 'true'
        if ev.event in ('rising',):
            return f'{d}.stable'
        if ev.event in ('falling',):
            return f'!{d}.stable'
        pressed_low = pull == 'up'
        if ev.event == 'press':
            return f'!{d}.stable' if pressed_low else f'{d}.stable'
        return f'{d}.stable' if pressed_low else f'!{d}.stable'

    def setup_loop(self):
        e = self.e
        info = self.info
        cores = sorted(info.cores_used | {DEFAULT_CORE})
        other = [c for c in cores if c != DEFAULT_CORE]
        if other:
            e('#if !defined(IOTIFT_SIM)')
            for c in other:
                e(f'static void iot_core{c}_main(void *) {{')
                e(f'  for (;;) {{ iot_poll_core{c}(); vTaskDelay(1); }}')
                e('}')
            e('#endif')
            e()
        e('void setup() {')
        e.indent()
        if info.uses_serial:
            e('Serial.begin(115200);')
        for p in info.pins:
            g = p.cname
            if p.kind == 'out':
                e(f'pinMode({g}, OUTPUT);')
                on = 'HIGH' if p.opts['active'] == 'high' else 'LOW'
                off = 'LOW' if on == 'HIGH' else 'HIGH'
                e(f'digitalWrite({g}, {on if p.opts["initial"] == "on" else off});')
            elif p.kind == 'in':
                mode = {'up': 'INPUT_PULLUP', 'down': 'INPUT_PULLDOWN', 'none': 'INPUT'}[p.opts['pull']]
                e(f'pinMode({g}, {mode});')
            elif p.kind == 'adc':
                if p.opts['atten'] != 11:
                    att = {0: 'ADC_0db', 2.5: 'ADC_2_5db', 6: 'ADC_6db'}[p.opts['atten']]
                    e(f'analogSetPinAttenuation({g}, {att});')
            elif p.kind == 'pwm':
                ch, f, b = p.channel, p.opts['freq'], p.opts['bits']
                e('#if ESP_ARDUINO_VERSION_MAJOR >= 3')
                e(f'ledcAttachChannel({g}, {f}, {b}, {ch});')
                e('#else')
                e(f'ledcSetup({ch}, {f}, {b});')
                e(f'ledcAttachPin({g}, {ch});')
                e('#endif')
        for ev in info.events:
            e(f'{ev.dname}.stable = {ev.dname}.cand = digitalRead({ev.pin.cname}) == HIGH;')
        for ev in info.system['boot']:
            e(f'{ev.cname}();')
        if info.wifi:
            e('iot_wifi_start();')
        e('[[maybe_unused]] const uint32_t now = millis();')
        for t in info.timers:
            period = t.period_us // 1000
            first = (t.offset_us // 1000) if t.offset_us is not None else period
            e(f'{t.tname} = {{now + {first}u, {period}u, true}};')
        for i, a in enumerate(info.afters):
            e(f'iot_once_{i} = {{now + {a.delay_us // 1000}u, false}};')
        for task in info.tasks:
            e(f'{task.cname}.wake = now;')
        if other:
            e('#if !defined(IOTIFT_SIM)')
            for c in other:
                e(f'xTaskCreatePinnedToCore(iot_core{c}_main, "iotift-core{c}", 8192, nullptr, 1, nullptr, {c});')
            e('#endif')
        for ev in info.system['ready']:
            e(f'{ev.cname}();')
        e.dedent()
        e('}')
        e()
        e('void loop() {')
        e.indent()
        e(f'iot_poll_core{DEFAULT_CORE}();')
        e('#if defined(IOTIFT_SIM)')
        for c in other:
            e(f'iot_poll_core{c}();')
        e('#else')
        e('vTaskDelay(1);  // let the idle task run; timers have 1 ms resolution')
        e('#endif')
        e.dedent()
        e('}')

    # ── statements ─────────────────────────────────────────────────────

    def block(self, stmts):
        for s in stmts:
            self.stmt(s)

    def sub_block(self, stmts):
        self.e.indent()
        self.block(stmts)
        self.e.dedent()

    def var_name(self, sym):
        if getattr(sym, 'cname', None):
            return sym.cname
        return u(sym.name)

    def stmt(self, s):
        e = self.e
        self.at_line(s)
        if isinstance(s, N.VarDecl):
            sym = s.sym
            if self.task is not None:
                if s.init is not None:
                    e(f'{sym.cname} = {self.expr(s.init)};')
                else:
                    e(f'{sym.cname} = {ctype(sym.ty)}{{}};')
                return
            const = 'const ' if not s.mutable else ''
            if s.init is not None:
                e(f'{const}{ctype(sym.ty)} {u(s.name)} = {self.expr(s.init)};')
            else:
                e(f'{ctype(sym.ty)} {u(s.name)}{{}};')
        elif isinstance(s, N.Assign):
            e(f'{self.expr(s.place)} {s.op} {self.expr(s.value)};')
        elif isinstance(s, N.ExprStmt):
            c = s.expr
            if isinstance(c, N.Call) and c.res == 'print':
                self.print_call(c)
            elif isinstance(c, N.Call) and c.res == 'shared':
                e(f'(void){self.expr(c)};')  # `add` yields the new value; unused here
            else:
                e(f'{self.expr(c)};')
        elif isinstance(s, N.If):
            for i, (cond, body) in enumerate(s.branches):
                kw = 'if' if i == 0 else '} else if'
                e(f'{kw} ({bare(self.expr(cond))}) {{')
                self.sub_block(body)
            if s.else_body is not None:
                e('} else {')
                self.sub_block(s.else_body)
            e('}')
        elif isinstance(s, N.While):
            e(f'while ({bare(self.expr(s.cond))}) {{')
            self.sub_block(s.body)
            e('}')
        elif isinstance(s, N.Loop):
            e('for (;;) {')
            self.sub_block(s.body)
            e('}')
        elif isinstance(s, N.For):
            self.for_stmt(s)
        elif isinstance(s, N.Match):
            self.match(s)
        elif isinstance(s, N.Break):
            e('break;')
        elif isinstance(s, N.Continue):
            e('continue;')
        elif isinstance(s, N.Pass):
            e(';')
        elif isinstance(s, N.Return):
            if self.task is not None:
                e('{ f.st = -1; return; }')
            elif s.value is None:
                e('return;')
            else:
                e(f'return {self.expr(s.value)};')
        elif isinstance(s, N.Wait):
            self.wait(s)
        else:
            self.fail(f'cannot generate {type(s).__name__}', s)

    def wait(self, s):
        e = self.e
        if self.task is None:
            self.fail('`wait` outside a task', s)
        self.state_n += 1
        k = self.state_n
        if s.kind == 'for':
            e(f'f.wake = millis() + iot::ms({self.expr(s.expr)});')
            e(f'f.st = {k};')
            e('return;')
            e.raw(f'case {k}:;')
        else:
            deadline = None
            if s.timeout is not None:
                deadline, _ = self.tmp(T.U32, 'deadline')
                e(f'{deadline} = millis() + iot::ms({self.expr(s.timeout)});')
            e(f'f.st = {k};')
            e.raw(f'case {k}:;')
            cond = f'!({self.expr(s.expr)})'
            if deadline:
                cond += f' && !iot::due(millis(), {deadline})'
            e(f'if ({cond}) {{ f.wake = millis() + 1; return; }}')

    def for_stmt(self, s):
        e = self.e
        var = self.var_name(s.sym) if self.task is not None else u(s.var)
        if isinstance(s.iter, N.RangeExpr):
            ty = s.iter.ty
            ct = ctype(ty)
            hi, decl = self.tmp(ty, 'end')
            lo = self.expr(s.iter.lo)
            step = self.expr(s.step) if s.step is not None else '1'
            cmp = '<=' if s.iter.inclusive else '<'
            if self.task is not None:
                e(f'{hi} = {self.expr(s.iter.hi)};')
                e(f'for ({var} = {lo}; {var} {cmp} {hi}; {var} += {step}) {{')
                self.sub_block(s.body)
                e('}')
            else:
                e('{')
                e.indent()
                e(f'const {ct} {hi} = {self.expr(s.iter.hi)};')
                e(f'for ({ct} {var} = {lo}; {var} {cmp} {hi}; {var} += {step}) {{')
                self.sub_block(s.body)
                e('}')
                e.dedent()
                e('}')
        else:
            arr_t = s.iter.ty
            idx, _ = self.tmp(T.USIZE, 'i')
            src = self.expr(s.iter)
            if self.task is not None:
                e(f'for ({idx} = 0; {idx} < {arr_t.size}; {idx}++) {{')
                e(f'  {var} = ({src}).v[{idx}];')
            else:
                e(f'for (size_t {idx} = 0; {idx} < {arr_t.size}; {idx}++) {{')
                e(f'  const {ctype(arr_t.elem)} {var} = ({src}).v[{idx}];')
            self.sub_block(s.body)
            e('}')

    def match(self, s):
        """`match` becomes an if/else chain (a C++ switch would capture the
        task's resume labels)."""
        e = self.e
        subj_t = s.subject.ty
        name, decl = self.tmp(subj_t, 'm')
        if decl:
            e('{')
            e.indent()
            e(f'const {decl} = {self.expr(s.subject)};')
        else:
            e(f'{name} = {self.expr(s.subject)};')
        first = True
        last_unguarded = s.arms[-1] if s.arms and s.arms[-1].guard is None else None
        for arm in s.arms:
            conds = []
            wild = False
            for p in arm.patterns:
                if isinstance(p, N.WildcardPat):
                    wild = True
                    break
                conds.append(self.pattern(p, name, subj_t))
            cond = 'true' if wild else conds[0] if len(conds) == 1 else ' || '.join(f'({c})' for c in conds)
            if arm.guard is not None:
                cond = f'({cond}) && ({bare(self.expr(arm.guard))})'
            # In an exhaustive match the last unguarded arm is the only one left:
            # make it a plain `else` so C++ sees every path covered.
            if arm is last_unguarded and not first:
                wild = True
            if wild and arm.guard is None:
                e('else {' if not first else '{')
            else:
                e(f'{"if" if first else "else if"} ({bare(cond)}) {{')
            self.sub_block(arm.body)
            e('}')
            first = False
            if wild and arm.guard is None:
                break
        if decl:
            e.dedent()
            e('}')

    def pattern(self, p, name, t):
        if getattr(p, 'res', None) == 'variant':
            return f'{name} == {t.cname}::{u(p.target)}'
        if isinstance(p, N.RangeExpr):
            op = '<=' if p.inclusive else '<'
            return f'{name} >= {self.expr(p.lo)} && {name} {op} {self.expr(p.hi)}'
        if t == T.STR:
            return f'strcmp({name}, {self.expr(p)}) == 0'
        return f'{name} == {self.expr(p)}'

    def print_call(self, c):
        # Values are worked out before anything is written, so a call that prints cannot land in
        # the middle of this line, and the line is written under a lock so the other core's output
        # cannot either.
        e = self.e
        pieces = []
        for a in c.ordered:
            if isinstance(a, N.StrLit):
                for part in a.parts:
                    if isinstance(part, N.Interpolation):
                        pieces.append((part.expr, part.fmt))
                    elif part:
                        pieces.append(part)
            else:
                pieces.append((a, None))
        e('{')
        e.indent()
        temps = []
        for p in pieces:
            if isinstance(p, tuple):
                t = f'iot_p{len(temps)}'
                e(f'const auto {t} = {self.expr(p[0])};')
                temps.append(t)
        e('iot::PrintLock iot_lock;')
        i = 0
        for p in pieces:
            if isinstance(p, tuple):
                e(self.put(p[0], p[1], temps[i]) + ';')
                i += 1
            else:
                e(f'iot::put_s({c_str(p)});')
        if c.target == 'println':
            e('iot::nl();')
        e.dedent()
        e('}')

    def put(self, x, fmt, v=None):
        t = x.ty
        v = v if v is not None else self.expr(x)
        if fmt in ('x', 'X'):
            return f'iot::put_hex((uint64_t)({v}), {"true" if fmt == "X" else "false"})'
        if fmt == 'b':
            return f'iot::put_bin((uint64_t)({v}))'
        if T.is_float(t):
            dec = int(fmt[1:]) if fmt else 2
            return f'iot::put_f((double)({v}), {dec})'
        if t == T.BOOL:
            return f'iot::put_b({v})'
        if t == T.STR:
            return f'iot::put_s({v})'
        if t == T.DURATION:
            return f'iot::put_dur({v})'
        if t == T.FREQUENCY:
            return f'iot::put_u({v}); iot::put_s("Hz")'
        if isinstance(t, T.EnumTy):
            return f'iot::put_s(iot_name({v}))'
        if T.is_int(t):
            if T.INT_INFO[T.default_of(t).name][0]:
                return f'iot::put_i((int64_t)({v}))'
            return f'iot::put_u((uint64_t)({v}))'
        self.fail(f'cannot print {t}', x)

    # ── expressions ────────────────────────────────────────────────────

    def expr(self, x):
        t = x.ty
        if isinstance(x, N.IntLit):
            v = self.checker.const_eval(x)
            if t == T.FREQUENCY:
                return f'{v}u'
            return int_lit(v, t)
        if isinstance(x, N.FloatLit):
            v = self.checker.const_eval(x)
            if t in (T.DURATION, T.FREQUENCY):
                return int_lit(int(v), t)
            return float_lit(v, t)
        if isinstance(x, N.BoolLit):
            return 'true' if x.value else 'false'
        if isinstance(x, N.StrLit):
            return c_str(''.join(p for p in x.parts if isinstance(p, str)))
        if isinstance(x, N.Name):
            return self.sym_ref(x.ref, t)
        if isinstance(x, N.SelfRef):
            return '(*this)'
        if isinstance(x, N.Unary):
            o = self.expr(x.operand)
            if x.op == 'not':
                return f'(!{o})'
            return f'({x.op}{o})'
        if isinstance(x, N.Binary):
            return self.binary(x)
        if isinstance(x, N.Call):
            return self.call(x)
        if isinstance(x, N.Member):
            return self.member(x)
        if isinstance(x, N.Index):
            it = x.index.ty
            cast = 'int64_t' if T.INT_INFO[T.default_of(it).name][0] else 'uint64_t'
            return f'iot::at({self.expr(x.obj)}, ({cast})({self.expr(x.index)}), "{self.src_name}", {x.line})'
        if isinstance(x, N.Cast):
            return f'static_cast<{ctype(t)}>({self.expr(x.expr)})'
        if isinstance(x, N.IfExpr):
            return f'({self.expr(x.cond)} ? {self.expr(x.then)} : {self.expr(x.other)})'
        if isinstance(x, N.ArrayLit):
            return f'{ctype(t)}{{{{' + ', '.join(self.expr(i) for i in x.items) + '}}'
        self.fail(f'cannot generate {type(x).__name__}', x)

    def sym_ref(self, sym, t):
        if sym.kind == 'const':
            v = sym.const_value
            if v is None:   # a C++ constant from a binding
                return sym.cname if sym.ty == t else f'static_cast<{ctype(t)}>({sym.cname})'
            if T.is_untyped(sym.ty) or T.is_untyped(t) or sym.ty != t:
                return float_lit(v, t) if isinstance(v, float) or T.is_float(t) else int_lit(v, t)
            return sym.cname
        return self.var_name(sym)

    def binary(self, x):
        op = x.op
        lt, rt = x.left.ty, x.right.ty
        a, b = self.expr(x.left), self.expr(x.right)
        if op == 'and':
            return f'({a} && {b})'
        if op == 'or':
            return f'({a} || {b})'
        if lt == T.STR and rt == T.STR:
            return f'(strcmp({a}, {b}) {op} 0)'
        if T.DURATION in (lt, rt) and op in ('*', '/') and lt != rt:
            other = rt if lt == T.DURATION else lt
            if T.is_float(other):
                return f'((int64_t)((double)({a}) {op} (double)({b})))'
            return f'((int64_t)({a}) {op} (int64_t)({b}))'
        return f'({a} {op} {b})'

    def args(self, c):
        return ', '.join(self.expr(a) for a in c.ordered)

    def call(self, c):
        res = c.res
        if res == 'fn':
            return f'{c.target.cname}({self.args(c)})'
        if res == 'method':
            return f'{self.expr(c.callee.obj)}.{u(c.target.name)}({self.args(c)})'
        if res == 'ext_method':
            return f'{self.expr(c.callee.obj)}.{c.target.cpp}({self.args(c)})'
        if res == 'static':
            return f'{c.target.owner.cname}::{u(c.target.name)}({self.args(c)})'
        if res == 'ctor':
            return f'{c.target.cname}{{' + self.args(c) + '}'
        if res == 'pin':
            return self.pin_call(c)
        if res == 'wifi':
            w, m = c.target
            return f'{w.cname}_{m}()'
        if res == 'timer':
            decl, m = c.target
            tn = self.timer_name(decl)
            if m == 'stop':
                return f'({tn}.running = false)'
            return f'({tn}.next = millis() + {tn}.period, {tn}.running = true)'
        if res == 'shared':
            decl, m = c.target
            n = decl.sym.cname
            if m == 'get':
                return f'{n}.load()'
            if m == 'set':
                return f'{n}.store({self.args(c)})'
            return f'({n}.fetch_add({self.args(c)}) + {self.args(c)})'
        if res == 'duration':
            v = self.expr(c.callee.obj)
            return {'as_us': f'((int64_t)({v}))', 'as_ms': f'((int64_t)({v}) / 1000)',
                    'as_s': f'((float)({v}) / 1000000.0f)'}[c.target]
        if res == 'len':
            return f'((size_t){c.target})'
        if res == 'builtin' or res == 'math':
            return self.builtin(c)
        if res == 'print':
            self.fail('`print` has no value', c)
        self.fail(f'cannot generate call ({res})', c)

    def builtin(self, c):
        name = c.target
        t = c.ty
        args = [self.expr(a) for a in c.ordered]
        if name == 'millis':
            return '((uint32_t)millis())'
        if name == 'micros':
            return '((uint32_t)micros())'
        if name == 'uptime':
            return 'iot::uptime()'
        ct = ctype(t)
        if name == 'abs':
            return f'({args[0]} < 0 ? ({ct})(-{args[0]}) : {args[0]})'
        if name == 'min':
            return f'({args[0]} < {args[1]} ? {args[0]} : {args[1]})'
        if name == 'max':
            return f'({args[0]} > {args[1]} ? {args[0]} : {args[1]})'
        if name == 'clamp':
            x, lo, hi = args
            return f'({x} < {lo} ? {lo} : ({x} > {hi} ? {hi} : {x}))'
        f = 'f' if t == T.F32 else ''
        if name == 'deg':
            return f'({args[0]} * ({ct})57.29577951308232)'
        if name == 'rad':
            return f'({args[0]} * ({ct})0.017453292519943295)'
        if name == 'lerp':
            a, b, k = args
            return f'({a} + ({b} - {a}) * {k})'
        if name == 'map_range':
            x, a, b, c2, d = args
            return f'({c2} + ({x} - {a}) * ({d} - {c2}) / ({b} - {a}))'
        cname = {'abs': 'fabs'}.get(name, name)
        return f'{cname}{f}({", ".join(args)})'

    def pin_call(self, c):
        decl, m = c.target
        g = decl.cname
        args = [self.expr(a) for a in c.ordered]
        if decl.kind == 'out':
            hi = decl.opts['active'] == 'high'
            on, off = ('HIGH', 'LOW') if hi else ('LOW', 'HIGH')
            if m == 'on':
                return f'digitalWrite({g}, {on})'
            if m == 'off':
                return f'digitalWrite({g}, {off})'
            if m == 'toggle':
                return f'digitalWrite({g}, !digitalRead({g}))'
            if m == 'set':
                return f'digitalWrite({g}, ({args[0]}) ? {on} : {off})'
            if m == 'is_on':
                return f'(digitalRead({g}) == {on})'
        if decl.kind == 'in':
            if m in ('level', 'is_high'):
                return f'(digitalRead({g}) == HIGH)'
            if m == 'is_low':
                return f'(digitalRead({g}) == LOW)'
            if m == 'is_pressed':
                lvl = 'LOW' if decl.opts['pull'] == 'up' else 'HIGH'
                return f'(digitalRead({g}) == {lvl})'
        if decl.kind == 'adc':
            if m == 'read':
                return f'((uint16_t)analogRead({g}))'
            if m == 'read_mv':
                return f'((uint32_t)analogReadMilliVolts({g}))'
        if decl.kind == 'pwm':
            ch = decl.channel
            mx = (1 << decl.opts['bits']) - 1
            if m == 'max':
                return f'{mx}u'
            if m == 'write':
                v = f'({args[0]} > {mx}u ? {mx}u : (uint32_t)({args[0]}))'
            else:
                r = args[0]
                v = f'(uint32_t)(({r}) <= 0.0f ? 0.0f : ({r}) >= 1.0f ? {mx}.0f : ({r}) * {mx}.0f + 0.5f)'
            return (f'iot_pwm_write({g}, {ch}, {v})')
        self.fail(f'unknown pin method {m}', c)

    def member(self, x):
        res = x.res
        if res == 'qualified':
            return self.sym_ref(x.ref, x.ty)
        if res == 'variant':
            return f'{x.ty.cname}::{u(x.target)}'
        if res == 'field':
            return f'{self.expr(x.obj)}.{u(x.name)}'
        if res == 'wifi_prop':
            w, p = x.target
            n = w.cname
            if p == 'connected':
                return f'({n}_state == iot_WifiState::connected_)'
            if p == 'state':
                return f'{n}_state'
            if p in ('ip', 'mac'):
                return f'static_cast<const char*>({n}_{p})'
            if p == 'rssi':
                return f'({n}_state == iot_WifiState::connected_ ? (int32_t)WiFi.RSSI() : (int32_t)0)'
            if p == 'clients':
                return '(uint8_t)WiFi.softAPgetStationNum()'
        if res == 'timer_running':
            return f'{self.timer_name(x.target)}.running'
        if res == 'math_const':
            v = {'pi': 3.141592653589793, 'e': 2.718281828459045, 'tau': 6.283185307179586}[x.target]
            return float_lit(v, x.ty if not T.is_untyped(x.ty) else T.F32)
        self.fail(f'cannot generate member ({res})', x)


def handlers_of(info, w, event):
    return [ev.cname for ev in info.wifi_events if ev.wifi is w and ev.event == event]


PWM_HELPER = '''
static inline void iot_pwm_write(int pin, int channel, uint32_t value) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)channel;
  ledcWrite(pin, value);
#else
  (void)pin;
  ledcWrite(channel, value);
#endif
}
'''


def generate(modules, info, checker, out_name='main.cpp', line_directives=True):
    cg = CodeGen(modules, info, checker, out_name, line_directives)
    code = cg.generate()
    if any(p.kind == 'pwm' for p in info.pins):
        marker = '}  // namespace iot\n'
        code = code.replace(marker, marker + PWM_HELPER, 1)
    return code
