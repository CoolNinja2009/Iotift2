"""From source files to C++: load modules, check, generate."""

import os

from . import nodes as N
from .parser import parse
from .checker import Checker, Module
from .codegen import generate
from .errors import Diagnostic, CompileError

BUILTIN_ROOTS = ('c', 'std')


class Project:
    """The entry file plus every module it `use`s, in dependency order."""

    def __init__(self, modules, sources, root=None):
        self.modules = modules          # dependencies first, entry last
        self.sources = sources          # display file name -> source text
        self.root = root                # folder of the entry file (None for in-memory tests)


def load(entry_source, entry_file='main.iot', files=None, root=None):
    """Parse the entry file and every module it uses.

    Modules are found relative to the entry file's folder: `use drivers.ntc`
    reads `drivers/ntc.iot`. `files` maps such relative paths to source text
    instead of reading the disk (used by tests).
    """
    root = root if root is not None else os.path.dirname(os.path.abspath(entry_file))
    sources = {}
    modules = []
    state = {}
    errors = []

    def read(rel):
        if files is not None:
            return files.get(rel)
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            return None
        with open(path, encoding='utf-8') as f:
            return f.read()

    def visit(name, display, source, entry):
        state[name] = 'loading'
        sources[display] = source
        try:
            prog = parse(source, display)
        except CompileError as e:
            errors.extend(e.diagnostics)
            state[name] = 'done'
            return
        for it in prog.items:
            if not isinstance(it, N.UseDecl) or it.path[0] in BUILTIN_ROOTS:
                continue
            dep = '.'.join(it.path)
            rel = '/'.join(it.path) + '.iot'
            st = state.get(dep)
            if st == 'loading':
                errors.append(Diagnostic(f'modules `{name}` and `{dep}` use each other', it.line,
                                         it.col, 'move the shared part into a third module',
                                         display))
                continue
            if st == 'done':
                continue
            src = read(rel)
            if src is None:
                errors.append(Diagnostic(f'cannot find module `{dep}`', it.line, it.col,
                                         f'expected the file {rel} next to '
                                         f'{os.path.basename(entry_file)}', display))
                state[dep] = 'done'
                continue
            visit(dep, rel, src, False)
        state[name] = 'done'
        modules.append(Module(name, prog, source, entry))

    visit('main', os.path.basename(entry_file), entry_source, True)
    if errors:
        raise CompileError(errors, sources)
    return Project(modules, sources, root if files is None else None)


def check_project(project):
    checker = Checker(project.modules, root=project.root)
    try:
        info = checker.check()
    except CompileError as e:
        raise CompileError(e.diagnostics, project.sources)
    return checker, info


def compile_source(source, file='main.iot', out_name='main.cpp', line_directives=True,
                   files=None, root=None):
    """Return C++ for an Iotift 2 program. Raises CompileError on any error."""
    project = load(source, file, files, root)
    checker, info = check_project(project)
    return generate(project.modules, info, checker, out_name, line_directives)


def check_source(source, file='main.iot', files=None, root=None):
    project = load(source, file, files, root)
    return check_project(project)[1]
