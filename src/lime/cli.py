from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console

from lime import __version__
from lime.config import load_settings
from lime.graphics import Headings, load_font
from lime.images import Images
from lime.mermaid import Mermaid
from lime.render import markdown_theme, render
from lime.terminal import Terminal, clean_text, query_palette


def positive_width(value: str) -> int:
    try:
        width = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("width must be an integer") from None
    if width < 12:
        raise argparse.ArgumentTypeError("width must be at least 12 columns")
    return width


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Render Markdown in Ghostty, with native scrolling and search.",
        epilog="Use Ghostty's scroll gesture and Cmd+F (macOS) / Ctrl+Shift+F (Linux).",
    )
    result.add_argument("file", metavar="DOC.md", help="Markdown file, or - to read stdin")
    result.add_argument("--version", action="version", version=f"lime {__version__}")
    result.add_argument("--width", type=positive_width, help="maximum output width (default: 88)")
    result.add_argument(
        "--config", type=Path, help="YAML configuration (default: ~/.config/lime/config.yaml)"
    )
    result.add_argument(
        "--line-numbers",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="code-block line numbers (default: enabled)",
    )
    result.add_argument("--mermaid", choices=("auto", "off"), default=None)
    result.add_argument("--images", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument(
        "--plain", action="store_true", help="text without colors, images or escapes"
    )
    result.add_argument(
        "--headings",
        choices=("auto", "image", "text"),
        default=None,
        help="heading style (default: images in Ghostty, text elsewhere)",
    )
    result.add_argument(
        "--heading-labels",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="searchable labels beneath image headings (default: enabled)",
    )
    result.add_argument("--font", type=Path, help="TTF/OTF font for enlarged headings")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        heading_mode = args.headings or settings.headings
        heading_labels = (
            settings.heading_labels if args.heading_labels is None else args.heading_labels
        )
        line_numbers = settings.line_numbers if args.line_numbers is None else args.line_numbers
        font = args.font or (Path(settings.font) if settings.font else None)
        path = Path(args.file).expanduser()
        source = sys.stdin.read() if args.file == "-" else path.read_text(encoding="utf-8-sig")
        if font:
            load_font(20, font)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"lime: {clean_text(str(error))}", file=sys.stderr)
        return 1

    terminal = Terminal.detect(sys.stdout)
    plain = args.plain or "NO_COLOR" in os.environ or not terminal.is_tty
    content_width = args.width or settings.width
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
        if native_theme and (args.mermaid or settings.mermaid) == "auto"
        else None
    )
    try:
        if source.strip() and vertical:
            console.print("\n" * vertical, end="")
        render(
            source,
            console,
            base=Path.cwd() if args.file == "-" else path.resolve().parent,
            margin=margin,
            headings=headings,
            heading_labels=heading_labels,
            line_numbers=line_numbers,
            mermaid=mermaid,
            images=Images(terminal, width - margin * 2)
            if graphics and (settings.images if args.images is None else args.images)
            else None,
        )
        if source.strip() and vertical:
            console.print("\n" * vertical, end="")
    except BrokenPipeError:
        # Avoid a second error from Python's final stdout flush after `| head`.
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, sys.stdout.fileno())
        os.close(null_fd)
        return 0
    except KeyboardInterrupt:
        return 130
    return 0
