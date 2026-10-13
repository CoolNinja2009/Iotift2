"""AST for Iotift 2. Every node has `line` and `col`.

The checker fills in `ty` on expressions and a few resolution fields
(`ref`, `res`, `target`) that the code generator reads.
"""

from dataclasses import dataclass
from typing import List, Optional, Any


class Node:
    # Position and the fields the checker fills in. Kept out of the dataclass
    # fields so constructors stay positional (and Python 3.8 compatible).
    line = 0
    col = 0
    ty = None        # expressions: resolved type
    ref = None       # names: resolved symbol
    sym = None       # declarations: their symbol
    target = None    # calls / members: what they resolve to
    ordered = None   # calls: arguments in declaration order
    res = None       # calls / members: resolution kind


# ── types ───────────────────────────────────────────────────────────────

@dataclass
class TypeName(Node):
    name: str


@dataclass
class ArrayTypeRef(Node):
    elem: Any
    size: Any  # expression (must be a compile-time constant)


# ── expressions ─────────────────────────────────────────────────────────

class Expr(Node):
    pass


@dataclass
class IntLit(Expr):
    value: int
    unit: Optional[str] = None


@dataclass
class FloatLit(Expr):
    value: float
    unit: Optional[str] = None


@dataclass
class BoolLit(Expr):
    value: bool


@dataclass
class StrLit(Expr):
    parts: List[Any]          # str | Interpolation


@dataclass
class Interpolation(Node):
    expr: Any
    fmt: Optional[str]


@dataclass
class Name(Expr):
    name: str


@dataclass
class SelfRef(Expr):
    pass


@dataclass
class Unary(Expr):
    op: str
    operand: Any


@dataclass
class Binary(Expr):
    op: str
    left: Any
    right: Any


@dataclass
class Arg(Node):
    name: Optional[str]
    value: Any


@dataclass
class Call(Expr):
    callee: Any
    args: List[Arg]


@dataclass
class Member(Expr):
    obj: Any
    name: str


@dataclass
class Index(Expr):
    obj: Any
    index: Any


@dataclass
class Cast(Expr):
    expr: Any
    to: Any


@dataclass
class IfExpr(Expr):
    cond: Any
    then: Any
    other: Any


@dataclass
class ArrayLit(Expr):
    items: List[Any]


@dataclass
class RangeExpr(Expr):
    lo: Any
    hi: Any
    inclusive: bool


# ── statements ──────────────────────────────────────────────────────────

@dataclass
class VarDecl(Node):
    mutable: bool
    name: str
    type: Any
    init: Any


@dataclass
class Assign(Node):
    place: Any
    op: str
    value: Any


@dataclass
class ExprStmt(Node):
    expr: Any


@dataclass
class If(Node):
    branches: List[Any]           # [(cond, body)]
    else_body: Optional[List[Any]]


@dataclass
class While(Node):
    cond: Any
    body: List[Any]


@dataclass
class For(Node):
    var: str
    iter: Any
    step: Any
    body: List[Any]


@dataclass
class Loop(Node):
    body: List[Any]


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Return(Node):
    value: Any


@dataclass
class Pass(Node):
    pass


@dataclass
class MatchArm(Node):
    patterns: List[Any]
    guard: Any
    body: List[Any]


@dataclass
class Match(Node):
    subject: Any
    arms: List[MatchArm]


@dataclass
class WildcardPat(Node):
    pass


@dataclass
class Wait(Node):
    kind: str          # 'for' (a duration) | 'until'
    expr: Any
    timeout: Any = None


# ── top-level items ─────────────────────────────────────────────────────

@dataclass
class Param(Node):
    name: str
    type: Any
    mutable: bool = False
    default: Any = None


@dataclass
class FnDecl(Node):
    name: str
    params: List[Param]
    ret: Any
    body: List[Any]
    pub: bool = False
    self_kind: Optional[str] = None    # None | 'self' | 'mut self'
    owner: Any = None                  # struct name for methods


@dataclass
class Field(Node):
    name: str
    type: Any
    default: Any


@dataclass
class StructDecl(Node):
    name: str
    fields: List[Field]
    pub: bool = False


@dataclass
class EnumDecl(Node):
    name: str
    variants: List[str]
    pub: bool = False


@dataclass
class ImplDecl(Node):
    type_name: str
    fns: List[FnDecl]


@dataclass
class GlobalDecl(Node):
    kind: str            # 'let' | 'var' | 'const' | 'shared'
    name: str
    type: Any
    init: Any
    pub: bool = False
    core: Any = None


@dataclass
class PinDecl(Node):
    name: str
    kind: str            # out in adc pwm
    number: Any
    options: dict        # option -> (value, line, col)


@dataclass
class DeviceDecl(Node):
    chip: str


@dataclass
class UseDecl(Node):
    path: List[str]
    alias: Optional[str]
    names: Optional[List[str]] = None   # use drivers.ntc (read, Ntc)


@dataclass
class Every(Node):
    period: Any
    offset: Any
    label: Optional[str]
    body: List[Any]
    core: Any = None


@dataclass
class After(Node):
    delay: Any
    body: List[Any]
    core: Any = None


@dataclass
class OnEvent(Node):
    source: Any          # Name of a pin, or None for system events
    event: str           # press release rising falling change | boot ready
    body: List[Any]
    core: Any = None


@dataclass
class TaskDecl(Node):
    name: str
    body: List[Any]
    core: Any = None


@dataclass
class WifiDecl(Node):
    """`wifi home do ssid: "x" ... end`. options: name -> (expr, line, col)."""
    name: str
    options: dict
    pub: bool = False


@dataclass
class CoreBlock(Node):
    core: int
    label: Optional[str]
    items: List[Any]


@dataclass
class ExternConst(Node):
    name: str
    type: Any
    cpp: str


@dataclass
class ExternObject(Node):
    name: str
    type: Any
    cpp: str
    mutable: bool


@dataclass
class ExternFn(Node):
    name: str
    params: List[Param]
    ret: Any
    cpp: str
    blocking: bool = False
    max_block: Any = None          # Duration expression for blocking(max: ...)
    self_kind: Optional[str] = None


@dataclass
class ExternClass(Node):
    name: str
    cpp: str
    ctor: Optional[List[Param]]
    methods: List[ExternFn]


@dataclass
class ExternBlock(Node):
    header: str
    lib: Optional[str]
    items: List[Any]
    pub: bool = False


@dataclass
class Program(Node):
    items: List[Any]
    file: str = '<input>'
