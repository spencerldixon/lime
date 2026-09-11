import io
import os
import subprocess
import sys

import pytest
from PIL import Image

from lime.cli import main
from lime.graphics import load_font
from lime.terminal import Terminal, TerminalTheme


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
    """Settings come only from the config file, so point XDG at one."""
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
    document.write_text("﻿# Café\n\nHello.")
    result = run(str(document))
    assert result.returncode == 0 and "Café" in result.stdout
    assert "﻿" not in result.stdout


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


def test_the_document_is_centred_with_vertical_padding(tmp_path, monkeypatch):
    document = tmp_path / "doc.md"
    document.write_text("A simple paragraph.")
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "width: 88\nvertical_padding: 6\n"))
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr(Terminal, "detect", lambda _: Terminal(columns=120, rows=40, is_tty=True))
    assert main([str(document)]) == 0
    lines = stream.getvalue().splitlines()
    assert lines[:6] == [""] * 6 and lines[-6:] == [""] * 6
    body = next(line for line in lines if "paragraph" in line)
    # margin = (120 - 88) // 2 == 16, on both sides.
    assert body.startswith(" " * 16) and body.endswith(" " * 16)


def test_a_window_narrower_than_the_measure_gets_no_margin(tmp_path, monkeypatch):
    document = tmp_path / "doc.md"
    document.write_text("Text.")
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "width: 88\n"))
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr(Terminal, "detect", lambda _: Terminal(columns=40, rows=40, is_tty=True))
    assert main([str(document)]) == 0
    body = next(line for line in stream.getvalue().splitlines() if "Text." in line)
    assert body.startswith("Text.")


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


def test_a_graphics_tty_enters_the_reader_for_a_headings_free_document(monkeypatch):
    called = []
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr("lime.cli.query_palette", lambda: None)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=True)
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("A document without headings.\n"))
    assert main(["-"]) == 0
    assert called


def test_empty_source_skips_the_reader(monkeypatch):
    called = []
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr("lime.cli.query_palette", lambda: None)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=True)
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["-"]) == 0
    assert not called


def test_stdin_is_named_in_the_reader(monkeypatch):
    called = []
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append((a, k)) or 0)
    monkeypatch.setattr("lime.cli.query_palette", lambda: None)
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=True)
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("# One\n"))
    assert main(["-"]) == 0
    assert called and called[0][0][2] == "stdin"


def test_the_reader_needs_a_graphics_tty(monkeypatch):
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


def test_no_color_never_enters_the_reader(monkeypatch):
    called = []
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("lime.cli.read", lambda *a, **k: called.append(a) or 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("# One\n"))
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=80, rows=24, is_tty=True, graphics=True)
    )
    assert main(["-"]) == 0
    assert not called


def test_render_caches_survive_resizes_but_not_document_sessions(tmp_path, monkeypatch):
    source = (
        "# One\n\n```mermaid\ngraph LR; A-->B\n```\n\n"
        "## Two\n\n```mermaid\ngraph LR; C-->D\n```\n"
    )
    diagram = io.BytesIO()
    Image.new("RGBA", (100, 50), "red").save(diagram, format="PNG")
    fonts, diagrams = [], []

    def font(size, custom=None):
        fonts.append(size)
        return load_font(size, custom)

    def png(self, source):
        diagrams.append(source)
        return diagram.getvalue()

    monkeypatch.setattr("lime.graphics.load_font", font)
    monkeypatch.setattr("lime.mermaid.Mermaid.png", png)
    monkeypatch.setenv("XDG_CONFIG_HOME", configured(tmp_path, "width: 88\n"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr(
        Terminal, "detect", lambda _: Terminal(columns=100, rows=40, is_tty=True, graphics=True)
    )
    monkeypatch.setattr(
        "lime.cli.query_palette",
        lambda: TerminalTheme(((10, 20, 30), (40, 50, 60), (70, 80, 90)), (255, 255, 255), (0, 0, 0)),
    )

    def reader(stream, layout, name, terminal, *, reprint):
        initial_fonts, initial_diagrams = len(fonts), len(diagrams)
        for columns, rows in [(120, 40), (120, 50)]:
            reprint(columns, rows)
            assert len(fonts) == initial_fonts  # Only margins / viewport height changed.
            assert len(diagrams) == initial_diagrams
        reprint(40, 50)
        assert len(fonts) == initial_fonts + 2  # Narrower content needs new heading images.
        assert len(diagrams) == initial_diagrams
        restored = reprint(100, 40)
        assert len(fonts) == initial_fonts + 2  # The earlier width is still cached.
        assert len(diagrams) == initial_diagrams
        assert restored == layout
        return 0

    monkeypatch.setattr("lime.cli.read", reader)
    for session in range(1, 3):
        monkeypatch.setattr("sys.stdin", io.StringIO(source))
        assert main(["-"]) == 0
        assert len(diagrams) == session * 2
        assert len(fonts) == session * 4
