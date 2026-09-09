"""Transient panels drawn into freshly reserved rows, covering nothing."""

from __future__ import annotations

from lime.outline import Section

MINIMUM = 6
SYNC_START = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"
KEYS = [
    ("t", "table of contents", "⌘F", "search"),
    ("n / p", "next / previous section", "⌘↑ ⌘↓", "section jump"),
    ("g / G", "top / bottom", "⌘C", "copy selection"),
    ("?", "this help", "", ""),
    ("q", "quit", "", ""),
]


def height(rows: int) -> int:
    """Rows available for an overlay's body: capped at 12, 0 below the floor of 6."""
    available = rows - 4
    return 0 if available < MINIMUM else min(12, available)


def reserve(rows: int) -> str:
    """Scroll fresh blank rows into place, then rewind the cursor to their top."""
    return "\n" * rows + f"\x1b[{rows}A"


def erase() -> str:
    """Clear the reserved block from the cursor to the end of the screen."""
    return "\r\x1b[J"


def frame(lines: list[str], rows: int) -> str:
    """Pad to exactly rows lines and return the cursor to the block's top."""
    lines = [*lines, *[""] * (rows - len(lines))][:rows]
    body = "\r\n".join(line + "\x1b[K" for line in lines)
    return f"{SYNC_START}\r{body}\r\x1b[{rows - 1}A{SYNC_END}"


def _fit(text: str, width: int) -> str:
    """Truncate with an ellipsis, or pad with spaces, to exactly width columns."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    return text[: max(0, width - 1)] + "…"


def _divider(label: str, width: int) -> str:
    """A dash-filled label, padded or truncated to exactly width columns."""
    content = f"─ {label} " if label else "─"
    if len(content) >= width:
        return content[:width]
    return content + "─" * (width - len(content))


def picker(
    entries: list[tuple[Section, tuple[int, ...]]],
    selected: int,
    query: str,
    width: int,
    rows: int,
) -> str:
    """A bordered jump-to-section list, scrolled to keep the selection in view."""
    total = height(rows)
    if not total:
        return ""
    inner = max(20, width - 4)
    lines = [
        "╭" + _divider("Jump to section", inner) + "╮",
        "│ > " + _fit(query, inner - 3) + "│",
        "├" + "─" * inner + "┤",
    ]
    body = total - 4
    if not entries:
        message = "No matching sections" if query else "No headings in this document"
        lines.append("│ " + _fit(message, inner - 1) + "│")
    else:
        start = max(0, min(selected - body + 1, len(entries) - body))
        for position, (section, _) in enumerate(entries[start : start + body]):
            index = start + position
            marker = "›" if index == selected else " "
            indent = "  " * (section.level - 1)
            label = f"H{section.level}"
            title = f"{marker} {indent}{section.title}"
            room = inner - len(label) - 1
            lines.append("│" + _fit(title, room) + label + " │")
    lines.append("╰" + _divider("↑/↓ move · Enter jump · Esc close", inner) + "╯")
    return frame(lines, total)


def help_text(width: int, rows: int, jump: str | None) -> str:
    """A bordered cheat sheet of lime and native Ghostty keys."""
    total = height(rows)
    if not total:
        return ""
    inner = max(20, width - 4)
    lines = ["╭" + _divider("Keys", inner) + "╮"]
    for key, action, native, native_action in KEYS:
        left = f"{key:<10}{action:<26}"
        right = f"{native} {native_action}" if native else ""
        lines.append("│ " + _fit(left + right, inner - 1) + "│")
    if jump:
        note = f"Jumping uses {jump} in this session."
        lines.append("│ " + _fit(note, inner - 1) + "│")
    lines.append("╰" + "─" * inner + "╯")
    return frame(lines, total)
