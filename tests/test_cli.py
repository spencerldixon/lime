import io
import os
import subprocess
import sys

import pytest

from lime.cli import main
from lime.terminal import Terminal


def run(*args, source=None):
    return subprocess.run(
        [sys.executable, "-m", "lime", *args],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )


def test_stdin_is_rendered_and_redirected_output_has_no_escapes():
    result = run("--headings", "image", "-", source="# Heading\n\nHello **world**.")
    assert result.returncode == 0
    assert "Heading" in result.stdout and "Hello world." in result.stdout
    assert "\x1b" not in result.stdout


def test_bom_and_unicode_file(tmp_path):
    document = tmp_path / "café.md"
    document.write_text("\ufeff# Café\n\nHello.")
    result = run(str(document))
    assert result.returncode == 0 and "Café" in result.stdout
    assert "\ufeff" not in result.stdout


def test_missing_file_is_a_useful_error(tmp_path):
    result = run(str(tmp_path / "missing.md"))
    assert result.returncode == 1
    assert "lime:" in result.stderr and "Traceback" not in result.stderr


def test_invalid_utf8_is_a_useful_error(tmp_path):
    document = tmp_path / "invalid.md"
    document.write_bytes(b"\xff")
    result = run(str(document))
    assert result.returncode == 1 and "Traceback" not in result.stderr


def test_version():
    assert run("--version").stdout.strip() == "lime 0.1.0"


@pytest.mark.parametrize("width", ["0", "-1", "hello"])
def test_invalid_width(width):
    assert run("--width", width, "-", source="test").returncode == 2


def test_empty_document():
    result = run("-", source="")
    assert result.returncode == 0 and not result.stdout.strip()


def test_terminal_padding_surrounds_document(tmp_path, monkeypatch):
    document = tmp_path / "doc.md"
    document.write_text("A simple paragraph.")
    configuration = tmp_path / "config.yaml"
    configuration.write_text("padding: 12\nvertical_padding: 6\n")
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr(Terminal, "detect", lambda _: Terminal(columns=120, rows=40, is_tty=True))
    assert main(["--config", str(configuration), "--plain", str(document)]) == 0
    lines = stream.getvalue().splitlines()
    assert lines[:6] == [""] * 6 and lines[-6:] == [""] * 6
    body = next(line for line in lines if "paragraph" in line)
    assert body.startswith(" " * 12) and body.endswith(" " * 12)


@pytest.mark.parametrize("multiplexer", ["TMUX", "STY", "ZELLIJ"])
def test_multiplexers_default_to_text(multiplexer):
    master, slave = os.openpty()
    try:
        with os.fdopen(os.dup(slave), "w") as stream:
            assert Terminal.detect(stream, {"TERM_PROGRAM": "ghostty"}).graphics
            assert not Terminal.detect(
                stream, {"TERM_PROGRAM": "ghostty", multiplexer: "yes"}
            ).graphics
    finally:
        os.close(master)
        os.close(slave)
