from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.console import Console

from lime.config import load_settings
from lime.graphics import Headings, load_font
from lime.images import Images
from lime.mermaid import Mermaid
from lime.reader import run as read
from lime.render import markdown_theme, render
from lime.terminal import Terminal, clean_text, query_palette

USAGE = "usage: lime DOC.md  (or - to read stdin)"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    file = argv[0]
    try:
        settings = load_settings()
        heading_mode = settings.headings
        heading_labels = settings.heading_labels
        line_numbers = settings.line_numbers
        font = Path(settings.font) if settings.font else None
        path = Path(file).expanduser()
        source = sys.stdin.read() if file == "-" else path.read_text(encoding="utf-8-sig")
        if font:
            load_font(20, font)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"lime: {clean_text(str(error))}", file=sys.stderr)
        return 1

    terminal = Terminal.detect(sys.stdout)
    plain = "NO_COLOR" in os.environ or not terminal.is_tty
    content_width = settings.width
    margin = min(settings.padding, max(0, (terminal.columns - 32) // 2)) if terminal.is_tty else 0
    width = (
        max(4, min(content_width + margin * 2, terminal.columns))
        if terminal.is_tty
        else content_width
    )
    vertical = (
        min(settings.vertical_padding, max(0, (terminal.rows - 8) // 2)) if terminal.is_tty else 0
    )
    graphics = not plain and terminal.rows >= 6 and (terminal.graphics or heading_mode == "image")
    console = Console(
        file=sys.stdout,
        width=width,
        force_terminal=terminal.is_tty and not plain,
        color_system=None if plain else "truecolor",
        no_color=plain,
        theme=markdown_theme(),
        highlight=False,
        markup=False,
    )
    native_theme = query_palette() if graphics else None
    headings = (
        Headings(terminal, width - margin * 2, native_theme.palette, font)
        if native_theme and heading_mode != "text"
        else None
    )
    mermaid = (
        Mermaid(terminal, width - margin * 2, native_theme)
        if native_theme and settings.mermaid == "auto"
        else None
    )
    render_options = {
        "base": Path.cwd() if file == "-" else path.resolve().parent,
        "margin": margin,
        "headings": headings,
        "heading_labels": heading_labels,
        "line_numbers": line_numbers,
        "mermaid": mermaid,
        "images": Images(terminal, width - margin * 2) if graphics and settings.images else None,
    }
    normalized_source = clean_text(source)

    try:
        if normalized_source.strip() and vertical:
            console.print("\n" * vertical, end="")
        sections = render(source, console, **render_options, marks=terminal.is_tty and not plain)
        if normalized_source.strip() and vertical:
            console.print("\n" * vertical, end="")

        interactive = {"auto": terminal.graphics, "on": True, "off": False}[settings.interactive]
        if interactive and normalized_source.strip() and not plain and terminal.is_tty:

            return read(
                sys.stdout,
                sections,
                path.name if file != "-" else "stdin",
                terminal,
            )
    except BrokenPipeError:
        # Avoid a second error from Python's final stdout flush after `| head`.
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, sys.stdout.fileno())
        os.close(null_fd)
        return 0
    except KeyboardInterrupt:
        return 130
    return 0
