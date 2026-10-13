"""The command line: file encoding, project layout and board detection for `upload`."""

import json
import subprocess
import types

import pytest

from helpers import ROOT  # noqa: F401  (puts the compiler on sys.path)
from iotift2 import cli


def test_build_writes_ascii_cpp(tmp_path):
    out = tmp_path / 'blink.cpp'
    cli.main(['build', str(ROOT / 'examples' / 'blink.iot'), '-o', str(out)])
    out.read_bytes().decode('ascii')  # raises if anything non-ASCII slipped in


def test_upload_wraps_cpp_file(tmp_path):
    cpp = tmp_path / 'app.cpp'
    cpp.write_text('void setup() {}\nvoid loop() {}\n')
    cli.write_cpp_project(str(cpp), str(tmp_path / 'proj'), None)
    assert (tmp_path / 'proj' / 'src' / 'main.cpp').read_text() == cpp.read_text()
    assert 'board = esp32dev' in (tmp_path / 'proj' / 'platformio.ini').read_text()


def fake_device_list(monkeypatch, ports):
    def run(*args, **kw):
        return types.SimpleNamespace(stdout=json.dumps(ports), returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)


def test_detects_only_esp_ports(monkeypatch):
    fake_device_list(monkeypatch, [
        {'port': 'COM1', 'description': 'Communications Port', 'hwid': 'ACPI\\PNP0501\\1'},
        {'port': 'COM5', 'description': 'Silicon Labs CP210x', 'hwid': 'USB VID:PID=10C4:EA60 SER=0001'},
    ])
    assert cli.esp_ports(['pio']) == [('COM5', 'Silicon Labs CP210x')]
    assert cli.pick_port(['pio']) == 'COM5'


def test_several_boards_need_port(monkeypatch, capsys):
    fake_device_list(monkeypatch, [
        {'port': 'COM5', 'description': 'CP210x', 'hwid': 'USB VID:PID=10C4:EA60'},
        {'port': 'COM7', 'description': 'USB JTAG/serial', 'hwid': 'USB VID:PID=303A:1001'},
    ])
    with pytest.raises(SystemExit):
        cli.pick_port(['pio'])
    assert 'COM7' in capsys.readouterr().err


def test_no_board_found(monkeypatch, capsys):
    fake_device_list(monkeypatch, [])
    with pytest.raises(SystemExit):
        cli.pick_port(['pio'])
    assert '--port' in capsys.readouterr().err


def test_typo_suggests_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(['vuild', 'x.iot'])
    assert 'Did you mean `build`?' in capsys.readouterr().err


def test_upload_installs_platformio_when_missing(monkeypatch, capsys):
    """A first upload with no PlatformIO installs it (no prompt without a terminal) and carries on."""
    installed = []
    monkeypatch.setattr(cli, 'find_pio', lambda: ['pio'] if installed else None)
    monkeypatch.setattr(cli, '_pip_install', lambda python, *extra: installed.append(python) or True)
    monkeypatch.delenv('IOTIFT_NO_AUTO_INSTALL', raising=False)
    assert cli.ensure_pio() == ['pio']
    assert installed
    assert 'PlatformIO is installed' in capsys.readouterr().out


def test_platformio_falls_back_to_private_env(monkeypatch):
    """When pip refuses the current Python (PEP 668), PlatformIO goes into ~/.platformio/penv."""
    calls = []
    monkeypatch.setattr(cli, 'find_pio', lambda: ['penv-pio'] if len(calls) > 1 else None)
    monkeypatch.setattr(cli, '_pip_install', lambda python, *extra: calls.append(python) or len(calls) > 1)
    monkeypatch.setattr(cli.subprocess, 'call', lambda cmd, **kw: 0)
    monkeypatch.delenv('IOTIFT_NO_AUTO_INSTALL', raising=False)
    assert cli.ensure_pio() == ['penv-pio']
    assert 'penv' in calls[1]


def test_auto_install_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(cli, 'find_pio', lambda: None)
    monkeypatch.setenv('IOTIFT_NO_AUTO_INSTALL', '1')
    with pytest.raises(SystemExit):
        cli.ensure_pio()


def test_declining_the_install_stops(monkeypatch):
    monkeypatch.setattr(cli, 'find_pio', lambda: None)
    monkeypatch.delenv('IOTIFT_NO_AUTO_INSTALL', raising=False)
    monkeypatch.delenv('IOTIFT_YES', raising=False)
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True, raising=False)
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    with pytest.raises(SystemExit):
        cli.ensure_pio()


def test_conditions_have_no_doubled_parentheses():
    """`if ((a == b))` is an error under clang's -Werror=parentheses (macOS CI); g++ does not mind."""
    from iotift2.compiler import compile_source
    src = (ROOT / 'examples' / 'two_cores.iot').read_text()
    code = compile_source(src, str(ROOT / 'examples' / 'two_cores.iot'))
    assert 'if ((' not in code and 'while ((' not in code
