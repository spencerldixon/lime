"""Full-screen panels drawn on the alternate screen, leaving the document alone."""

from __future__ import annotations

import re

from rich.cells import cell_len

from lime.outline import Section, search

SYNC_START = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"
# The alternate screen has its own buffer, so a panel adds nothing to scrollback
# and the document underneath is restored untouched when the panel closes.
ENTER = "\x1b[?1049h\x1b[?25l"
LEAVE = "\x1b[?25h\x1b[?1049l"


def screen(lines: list[str], rows: int) -> str:
    """Paint exactly rows lines from the top of the alternate screen."""
    lines = [*lines, *[""] * (rows - len(lines))][:rows]
    body = "\r\n".join(line + "\x1b[K" for line in lines)
    return f"{SYNC_START}\x1b[H{body}{SYNC_END}"


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def visible_width(text: str) -> int:
    """Terminal-cell width, ignoring any SGR the caller has already applied."""
    return cell_len(_ANSI.sub("", text))


def _fit(text: str, width: int) -> str:
    """Truncate with an ellipsis, or pad with spaces, to exactly width columns."""
    if width <= 0:
        return ""
    if visible_width(text) <= width:
        return text + " " * (width - visible_width(text))
    limit = max(0, width - cell_len("…"))
    result = ""
    used = 0
    index = 0
    while index < len(text) and used < limit:
        if text[index] == "\x1b":
            match = _ANSI.match(text, index)
            if match:
                result += match.group()
                index = match.end()
                continue
        character = text[index]
        cells = cell_len(character)
        if used + cells > limit:
            break
        result += character
        used += cells
        index += 1
    return result + "\x1b[0m…" + " " * (width - used - cell_len("…"))


def _divider(label: str, width: int) -> str:
    """A dash-filled label, padded or truncated to exactly width columns."""
    content = f"─ {label} " if label else "─"
    if visible_width(content) >= width:
        return _fit(content, width)
    return content + "─" * (width - visible_width(content))


def window(selected: int, count: int, body: int) -> int:
    """First row to show, keeping the selection centred without running past an end."""
    return max(0, min(selected - body // 2, count - body))


# Foreground SGR per heading level, kept in step with markdown_theme() in
# render.py so a heading wears the same colour in the outline as in the document.
_LEVEL_SGR: dict[int, str] = {
    1: "\x1b[1;32m",  # bold green
    2: "\x1b[1;36m",  # bold cyan
    3: "\x1b[1;34m",  # bold blue
    4: "\x1b[1m",  # bold
    5: "\x1b[1m",  # bold
    6: "\x1b[2;1m",  # dim bold
}
_SGR_RESET = "\x1b[0m"


def _tint(line: str, level: int) -> str:
    """Colour a whole row by its heading level, leaving unknown levels plain."""
    sgr = _LEVEL_SGR.get(level, "")
    return f"{sgr}{line}{_SGR_RESET}" if sgr else line


def _heading(matches: list[Section], sections: list[Section], query: str) -> str:
    """The top divider label: a plain count, or the live filter and its tally."""
    if not query.strip():
        return f"Contents · {len(sections)}"
    return f"Contents · {len(matches)}/{len(sections)} · {query}▏"


def contents(sections: list[Section], selected: int, width: int, rows: int, query: str = "") -> str:
    """The outline narrowed to `query`, with the selected match as a lit row."""
    body = max(1, rows - 2)
    matches = search(sections, query)
    # Indent relative to the shallowest heading in the whole document, so rows do
    # not shift sideways as the filter changes, nor a wholly-H2 outline sit adrift
    # of the left margin.
    shallowest = min((section.level for section in sections), default=1)
    selected = min(selected, len(matches) - 1) if matches else 0
    start = window(selected, len(matches), body)
    lines = []
    for index in range(start, min(start + body, len(matches))):
        section = matches[index]
        line = _fit("  " + "  " * (section.level - shallowest) + section.title, width)
        if index == selected:
            lines.append(f"\x1b[7m{line}\x1b[27m")
        else:
            lines.append(_tint(line, section.level))
    if not sections:
        lines.append(_fit("  No headings in this document", width))
    elif not matches:
        lines.append(_fit(f'  Nothing matches "{query}"', width))
    lines += [""] * (body - len(lines))
    return screen(
        [
            _divider(_heading(matches, sections, query), width),
            *lines[:body],
            _divider("↑↓ move · ⏎ jump · esc close · type to filter", width),
        ],
        rows,
    )


SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("t", "Table of contents (type to filter)"),
    ("n / p", "Next / Previous heading"),
    ("g / G", "Go to beginning / end of document"),
    ("q, Ctrl-C, Ctrl-D", "Quit"),
    ("?", "Keyboard shortcuts"),
)


def shortcuts(width: int, rows: int) -> str:
    """The full list of key bindings, one per row, on the alternate screen."""
    body = max(1, rows - 2)
    pad = max(len(key) for key, _ in SHORTCUTS)
    lines = [_fit(f"  {key.ljust(pad)}  {label}", width) for key, label in SHORTCUTS]
    lines += [""] * (body - len(lines))
    return screen(
        [
            _divider("Keyboard shortcuts", width),
            *lines[:body],
            _divider("Any key closes", width),
        ],
        rows,
    )
