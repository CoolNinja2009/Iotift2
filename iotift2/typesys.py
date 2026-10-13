"""The Iotift 2 type system."""


class Ty:
    name = '?'

    def __repr__(self):
        return self.name

    __str__ = __repr__


class Prim(Ty):
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Prim) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


class Untyped(Ty):
    """Type of a literal before context gives it one ({integer} or {float})."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Untyped) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


class ArrayTy(Ty):
    def __init__(self, elem, size):
        self.elem = elem
        self.size = size
        self.name = f'[{elem}; {size}]'

    def __eq__(self, other):
        return isinstance(other, ArrayTy) and other.elem == self.elem and other.size == self.size

    def __hash__(self):
        return hash(('arr', self.elem, self.size))


class StructTy(Ty):
    def __init__(self, name, decl):
        self.name = name
        self.decl = decl
        self.fields = {}      # name -> (Ty, Field)
        self.methods = {}     # name -> FnDecl


class ClassTy(Ty):
    """A C++ class bound with `extern cpp`. Values are objects that live at
    the top level of a module and are passed by reference; never copied."""

    def __init__(self, name, cpp, decl):
        self.name = name
        self.cpp = cpp
        self.cname = cpp
        self.decl = decl
        self.methods = {}     # name -> ExternFn
        self.ctor = None      # list of Param, or None for a default constructor


class EnumTy(Ty):
    def __init__(self, name, decl):
        self.name = name
        self.decl = decl
        self.variants = list(decl.variants)


class PinTy(Ty):
    def __init__(self, kind):
        self.kind = kind
        self.name = f'{kind} pin'

    def __eq__(self, other):
        return isinstance(other, PinTy) and other.kind == self.kind

    def __hash__(self):
        return hash(('pin', self.kind))


class SpecialTy(Ty):
    """Things that are names but not values: timers, tasks, modules, types."""

    def __init__(self, name):
        self.name = name


I8, I16, I32, I64 = Prim('i8'), Prim('i16'), Prim('i32'), Prim('i64')
U8, U16, U32, U64 = Prim('u8'), Prim('u16'), Prim('u32'), Prim('u64')
USIZE = Prim('usize')
F32, F64 = Prim('f32'), Prim('f64')
BOOL = Prim('bool')
STR = Prim('str')
DURATION = Prim('Duration')
FREQUENCY = Prim('Frequency')
VOID = Prim('void')
UINT = Untyped('{integer}')
UFLOAT = Untyped('{float}')
TIMER = SpecialTy('timer')
TASK = SpecialTy('task')

PRIMS = {t.name: t for t in (I8, I16, I32, I64, U8, U16, U32, U64, USIZE, F32, F64, BOOL, STR,
                             DURATION, FREQUENCY)}
PRIMS['byte'] = U8

# name -> (signed, bits)
INT_INFO = {'i8': (True, 8), 'i16': (True, 16), 'i32': (True, 32), 'i64': (True, 64),
            'u8': (False, 8), 'u16': (False, 16), 'u32': (False, 32), 'u64': (False, 64),
            'usize': (False, 32)}

CPP = {'i8': 'int8_t', 'i16': 'int16_t', 'i32': 'int32_t', 'i64': 'int64_t',
       'u8': 'uint8_t', 'u16': 'uint16_t', 'u32': 'uint32_t', 'u64': 'uint64_t', 'usize': 'size_t',
       'f32': 'float', 'f64': 'double', 'bool': 'bool', 'str': 'const char*',
       'Duration': 'int64_t', 'Frequency': 'uint32_t', 'void': 'void'}


def is_int(t):
    return t == UINT or (isinstance(t, Prim) and t.name in INT_INFO)


def is_float(t):
    return t == UFLOAT or t == F32 or t == F64


def is_num(t):
    return is_int(t) or is_float(t)


def is_untyped(t):
    return isinstance(t, Untyped)


def int_range(t):
    signed, bits = INT_INFO[t.name]
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def fits(value, t):
    if is_float(t):
        return True
    if isinstance(t, Prim) and t.name in INT_INFO:
        lo, hi = int_range(t)
        return lo <= value <= hi
    return False


def widens(src, dst):
    """Can a value of type src be used where dst is expected, without a cast?"""
    if src == dst:
        return True
    if isinstance(src, Prim) and isinstance(dst, Prim):
        if src.name in INT_INFO and dst.name in INT_INFO:
            ss, sb = INT_INFO[src.name]
            ds, db = INT_INFO[dst.name]
            if ss == ds:
                return db >= sb
            if not ss and ds:
                return db > sb
            return False
        if src == F32 and dst == F64:
            return True
    return False


def default_of(t):
    """The concrete type an untyped literal gets when nothing else decides."""
    if t == UINT:
        return I32
    if t == UFLOAT:
        return F32
    return t
