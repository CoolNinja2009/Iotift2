"""Shared test helpers: compile Iotift 2 source, build it against the host
simulator (tests/sim) and run it, so tests check behaviour, not text."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from iotift2.compiler import compile_source, check_source  # noqa: E402
from iotift2.errors import CompileError  # noqa: E402

SIM = Path(__file__).resolve().parent / 'sim'
CXX = shutil.which('g++') or shutil.which('clang++')
WORK = Path(tempfile.mkdtemp(prefix='iotift2_t_'))
_SIM_OBJ = {}
_N = [0]


def errors(source, files=None):
    """Messages of the compile errors for *source* (empty list if it compiles).
    *files* maps module paths such as 'drivers/ntc.iot' to their source."""
    try:
        check_source(source, files=files if files is not None else {})
    except CompileError as e:
        return [d.message for d in e.diagnostics]
    return []


def error_text(source, files=None):
    """Messages and help lines of every error, one per line."""
    try:
        check_source(source, files=files if files is not None else {})
    except CompileError as e:
        return '\n'.join(d.message + (f' | help: {d.help}' if d.help else '') for d in e.diagnostics)
    return ''


def sim_obj(flags):
    key = tuple(flags)
    if key not in _SIM_OBJ:
        obj = WORK / f'sim{len(_SIM_OBJ)}.o'
        subprocess.run([CXX, '-std=c++17', '-DIOTIFT_SIM', *flags, '-c', '-I', str(SIM),
                        str(SIM / 'sim.cpp'), '-o', str(obj)], check=True, capture_output=True)
        _SIM_OBJ[key] = obj
    return _SIM_OBJ[key]


def build(source, flags=(), files=None, path=None):
    if CXX is None:
        pytest.skip('no C++ compiler on PATH')
    if path is not None:      # a real file: its modules are read from disk
        code = compile_source(source, str(path))
    else:
        code = compile_source(source, 'test.iot', files=files if files is not None else {})
    _N[0] += 1
    cpp = WORK / f'p{_N[0]}.cpp'
    exe = WORK / f'p{_N[0]}'
    cpp.write_text(code)
    r = subprocess.run([CXX, '-std=c++17', '-Wall', '-Wextra', '-Werror=return-type',
                        '-Werror=parentheses', '-Wno-unused-parameter', '-Wno-unused-variable',
                        '-Wno-unused-function', '-DIOTIFT_SIM', *flags, '-I', str(SIM), '-I', str(SIM / 'libs'), str(cpp),
                        str(sim_obj(flags)), '-o', str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, f'generated C++ does not compile:\n{r.stderr}\n--- code ---\n{code}'
    return exe


def run(source, ms, events='', flags=(), expect_code=0, files=None, path=None):
    exe = build(source, flags, files, path)
    r = subprocess.run([str(exe), str(ms), events], capture_output=True, text=True, timeout=60)
    assert r.returncode == expect_code, r.stdout + r.stderr
    return r.stdout.splitlines()


def serial(lines):
    out = []
    for ln in lines:
        if '] SERIAL: ' in ln:
            t, text = ln.split('] SERIAL: ', 1)
            out.append((int(t[1:]), text))
    return out


def texts(lines):
    return [t for _, t in serial(lines)]


def pin_changes(lines, gpio):
    out = []
    tag = f'] PIN {gpio} -> '
    for ln in lines:
        if tag in ln:
            t, v = ln.split(tag)
            out.append((int(t[1:]), int(v)))
    return out
