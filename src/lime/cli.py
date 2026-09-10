from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from lime.config import load_settings
from lime.graphics import Headings
from lime.images import Images
from lime.mermaid import Mermaid
from lime.reader import Layout
from lime.reader import run as read
from lime.render import RowCounter, markdown_theme, render
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
        path = Path(file).expanduser()
        source = sys.stdin.read() if file == "-" else path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"lime: {clean_text(str(error))}", file=sys.stderr)
        return 1

    terminal = Terminal.detect(sys.stdout)
    plain = "NO_COLOR" in os.environ or not terminal.is_tty
    content_width = settings.width
    graphics = not plain and terminal.rows >= 6 and terminal.graphics
    native_theme = query_palette() if graphics else None
    normalized_source = clean_text(source)

    def print_document(columns: int, rows: int) -> Layout:
        """Render the document once at the given size; report its heading rows.

        The document is a fixed measure centred in the window, so a wider window
        adds an even margin rather than stretching the text. `Layout.anchors`
        holds the output row of the document start and each top-level heading,
        which the reader scrolls to after a reflow.
        """
        local = replace(terminal, columns=columns, rows=rows)
        vertical = (
            min(settings.vertical_padding, max(0, (rows - 8) // 2)) if terminal.is_tty else 0
        )
        margin = (
            max(0, (columns - min(content_width, columns)) // 2) if terminal.is_tty else 0
        )
        width = (
            max(4, min(content_width + margin * 2, columns)) if terminal.is_tty else content_width
        )
        counter = RowCounter(sys.stdout)
        console = Console(
            file=counter,
            width=width,
            force_terminal=terminal.is_tty and not plain,
            color_system=None if plain else "truecolor",
            no_color=plain,
            theme=markdown_theme(),
            highlight=False,
            markup=False,
        )
        headings = Headings(local, width - margin * 2, native_theme.palette) if native_theme else None
        mermaid = Mermaid(local, width - margin * 2, native_theme) if native_theme else None
        anchors: list[int] = []
        render_options = {
            "base": Path.cwd() if file == "-" else path.resolve().parent,
            "margin": margin,
            "headings": headings,
            "heading_labels": True,
            "line_numbers": True,
            "anchors": anchors,
            "mermaid": mermaid,
            "images": Images(local, width - margin * 2) if graphics else None,
        }
        if normalized_source.strip() and vertical:
            console.print("\n" * vertical, end="")
        sections = render(source, console, **render_options)
        if normalized_source.strip() and vertical:
            console.print("\n" * vertical, end="")
        return Layout(sections, anchors)

    interactive = (
        terminal.graphics and normalized_source.strip() and not plain and terminal.is_tty
    )
    try:
        if interactive:
            # The reader navigates by absolute scrollback row, so the document
            # has to begin at row 0: clear the screen and scrollback, then print
            # once. (A resize reflow does the same before every reprint.)
            sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
            sys.stdout.flush()
        layout = print_document(terminal.columns, terminal.rows)
        if interactive:
            return read(
                sys.stdout,
                layout,
                path.name if file != "-" else "stdin",
                terminal,
                reprint=print_document,
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
