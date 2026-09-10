import io
import os
import subprocess
import sys

import pytest

from lime.cli import main
from lime.terminal import Terminal


def run(*args, source=None, env=None):
    return subprocess.run(
        [sys.executable, "-m", "lime", *args],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def configured(tmp_path, yaml):
    """Settings now come only from the config file, so point XDG at one."""
    directory = tmp_path / "lime"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(yaml)
    return str(tmp_path)


def test_stdin_is_rendered_and_redirected_output_has_no_escapes():
    result = run("-", source="# Heading\n\nHello **world**.")
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


def test_empty_document():
    result = run("-", source="")
    assert result.returncode == 0 and not result.stdout.strip()


def test_terminal_padding_surrounds_document(tmp_path, monkeypatch):
    document = tmp_path / "doc.md"
    document.write_text("A simple paragraph.")
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "padding: 12\nvertical_padding: 6\n"))
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr(Terminal, "detect", lambda _: Terminal(columns=120, rows=40, is_tty=True))
    assert main([str(document)]) == 0
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


def test_piped_output_never_enters_the_reader():
    result = run("-", source="# One\n\n## Two\n")
    assert result.returncode == 0 and "\x1b" not in result.stdout


def test_reader_is_skipped_without_a_tty(tmp_path, monkeypatch):
    document = tmp_path / "doc.md"
    document.write_text("# One\n")
    called = []
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=False, graphics=False)
    )
    assert main([str(document)]) == 0
    assert not called


def test_configured_interactive_enters_for_headings_free_document(monkeypatch, tmp_path):
    called = []
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "interactive: on\n"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=False)
    )
    monkeypatch.setattr("lime.cli.query_palette", lambda: None)
    monkeypatch.setattr("sys.stdin", io.StringIO("A document without headings.\n"))
    assert main(["-"]) == 0
    assert called


def test_empty_source_skips_reader_even_when_configured_on(monkeypatch, tmp_path):
    called = []
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "interactive: on\n"))
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=False)
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["-"]) == 0
    assert not called


def test_stdin_is_named_in_the_reader(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "interactive: on\n"))
    called = []
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=False)
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# One\n"))
    assert main(["-"]) == 0
    assert called and called[0][0][2] == "stdin"


def test_auto_interactive_requires_graphics_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lime.cli.query_palette", lambda: None)
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("# One\n"))
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=True)
    )
    assert main(["-"]) == 0

    called = []
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append(a) or 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("# One\n"))
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=False)
    )
    assert main(["-"]) == 0
    assert not called


def test_no_color_never_enters_the_reader(monkeypatch, tmp_path):
    called = []
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "interactive: on\n"))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append(a) or 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("# One\n"))
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=True)
    )
    assert main(["-"]) == 0
    assert not called
