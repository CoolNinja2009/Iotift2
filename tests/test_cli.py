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
