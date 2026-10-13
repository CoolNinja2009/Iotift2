"""Diagnostics shared by every compiler pass."""


class Diagnostic:
    def __init__(self, message, line=0, col=0, help=None, file='<input>', severity='error'):
        self.message = message
        self.line = line
        self.col = col
        self.help = help
        self.file = file
        self.severity = severity

    def render(self, source=None):
        out = f'{self.severity}: {self.message}\n  --> {self.file}:{self.line}:{self.col}'
        if source is not None and self.line:
            lines = source.splitlines()
            if 0 < self.line <= len(lines):
                text = lines[self.line - 1]
                num = str(self.line)
                pad = ' ' * len(num)
                out += f'\n {pad} |\n {num} | {text}\n {pad} | ' + ' ' * max(self.col - 1, 0) + '^'
        if self.help:
            out += f'\n  = help: {self.help}'
        return out

    def __str__(self):
        return f'{self.file}:{self.line}:{self.col}: {self.severity}: {self.message}' + \
            (f' (help: {self.help})' if self.help else '')

    __repr__ = __str__


class CompileError(Exception):
    """One or more fatal diagnostics."""

    def __init__(self, diagnostics, sources=None):
        if isinstance(diagnostics, Diagnostic):
            diagnostics = [diagnostics]
        self.diagnostics = list(diagnostics)
        self.sources = sources or {}      # file name -> source text, for rendering
        super().__init__('\n'.join(str(d) for d in self.diagnostics))

    def render(self, source=None):
        out = []
        for d in self.diagnostics:
            src = self.sources.get(d.file, source if not self.sources else None)
            out.append(d.render(src))
        return '\n\n'.join(out)
