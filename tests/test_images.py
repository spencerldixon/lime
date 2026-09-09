import io
import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from rich.console import Console

from lime.images import Images, write_image
from lime.mermaid import Mermaid
from lime.render import render
from lime.terminal import Terminal, TerminalTheme

THEME = TerminalTheme(((20, 30, 40), (50, 60, 70), (80, 90, 100)), (220, 220, 220), (30, 30, 30))


def test_local_image_and_alt_text_are_rendered(tmp_path):
    Image.new("RGB", (100, 70), "red").save(tmp_path / "example.png")
    stream = io.StringIO()
    render(
        "![Searchable caption](example.png)",
        Console(file=stream),
        base=tmp_path,
        images=Images(Terminal(), 80),
    )
    assert "\x1b_G" in stream.getvalue()
    assert "Searchable caption" in stream.getvalue()


def test_missing_and_remote_images_remain_readable(tmp_path):
    stream = io.StringIO()
    render(
        "![Missing](missing.png)\n\n![Remote](https://example.com/image.png)",
        Console(file=stream),
        base=tmp_path,
        images=Images(Terminal(), 80),
    )
    assert "\x1b_G" not in stream.getvalue()
    assert "Missing" in stream.getvalue() and "Remote" in stream.getvalue()


def test_tall_images_are_sliced_within_screen_height():
    stream = io.StringIO()
    write_image(stream, Image.new("RGBA", (120, 1600)), Terminal(rows=10), 30, 2)
    rows = [int(value) for value in re.findall(r",r=(\d+),", stream.getvalue())]
    assert len(rows) > 1 and max(rows) <= 8


def test_mermaid_missing_dependency_falls_back_to_source(monkeypatch):
    monkeypatch.setattr("lime.mermaid.shutil.which", lambda _: None)
    stream = io.StringIO()
    render(
        "```mermaid\ngraph LR\n  Start --> End\n```",
        Console(file=stream),
        base=Path.cwd(),
        mermaid=Mermaid(Terminal(), 80, THEME),
    )
    assert "Start --> End" in stream.getvalue()
    assert "install @mermaid-js/mermaid-cli" in stream.getvalue()


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("mmdc", 30), subprocess.CalledProcessError(1, "mmdc")]
)
def test_mermaid_renderer_failure_is_readable(monkeypatch, error):
    monkeypatch.setattr("lime.mermaid.shutil.which", lambda _: "/fake/mmdc")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("lime.mermaid.subprocess.run", fail)
    with pytest.raises(ValueError, match="showing source"):
        Mermaid(Terminal(), 80, THEME).png("invalid")
