# Interactive Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give lime a table of contents, section jumping, a control bar, and a help modal, without giving up Ghostty's native scrollback, selection, or search.

**Architecture:** Lime never takes the viewport. It prints `OSC 133;A` prompt marks before each heading so Ghostty's own `jump_to_prompt` can walk sections, stays alive reading `/dev/tty` in raw mode for keys, and asks Ghostty to move its own viewport through AppleScript `perform action`. Overlays paint into freshly reserved rows at the bottom of the screen so nothing already printed is ever covered.

**Tech Stack:** Python 3.11+, markdown-it-py, Rich, Pillow, ruamel.yaml, pytest, ruff. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-interactive-navigation-design.md`

## Global Constraints

- Python `>=3.11`. Ruff `line-length = 100`, `target-version = "py311"`. Run `uv run ruff check src tests` before every commit.
- **No new runtime dependencies.** The Homebrew formula pins exact checksums per dependency; adding one is a packaging change outside this plan.
- **Non-TTY output must stay byte-identical to v0.1.** Every existing test in `tests/` must pass unchanged at every commit. No escape sequence — marks included — is emitted when stdout is not a TTY.
- **Terminal state is restored on every exit path**, including `SIGTERM`, `SIGHUP`, and uncaught exceptions: `termios` attributes, window title, and any open overlay.
- **No polling.** Redraw on keystroke and on `SIGWINCH` only.
- Marks are emitted for headings at `token.level == 0` only. `outline()` uses the identical predicate, so mark order and outline index can never diverge. This correspondence is what makes the jump correct.
- Prompt mark bytes are exactly `\x1b]133;A\x1b\\`.
- Jump is `scroll_to_bottom` then `jump_to_prompt:-(total - index - 1)`, re-anchored every time.
- Docstring style: one line, lowercase after the first word, describing intent not mechanics. Match the surrounding files.

---

### Task 1: Outline model and filter

Pure functions over markdown-it tokens. No terminal, no I/O.

**Files:**
- Create: `src/lime/outline.py`
- Test: `tests/test_outline.py`

**Interfaces:**
- Consumes: `markdown_it.token.Token`; `lime.render.heading_text` (already exists at `render.py:135`)
- Produces:
  - `Section(title: str, level: int, index: int, line: int)` frozen dataclass
  - `outline(tokens: list[Token]) -> list[Section]`
  - `match(query: str, title: str) -> tuple[int, ...] | None`
  - `filtered(sections: list[Section], query: str) -> list[tuple[Section, tuple[int, ...]]]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_outline.py
from markdown_it import MarkdownIt

from lime.outline import Section, filtered, match, outline


def parse(source):
    return MarkdownIt("commonmark", {"html": False}).enable(["strikethrough", "table"]).parse(source)


def test_outline_captures_level_title_and_order():
    sections = outline(parse("# One\n\ntext\n\n## Two\n\n### Three\n"))
    assert sections == [
        Section("One", 1, 0, 0),
        Section("Two", 2, 1, 4),
        Section("Three", 3, 2, 6),
    ]


def test_duplicate_titles_stay_distinct_by_line():
    sections = outline(parse("## Install\n\nfirst\n\n## Install\n\nsecond\n"))
    assert [section.line for section in sections] == [0, 4]
    assert [section.index for section in sections] == [0, 1]


def test_inline_formatting_is_flattened():
    assert outline(parse("# A `code` and **bold**\n"))[0].title == "A code and bold"


def test_headings_inside_lists_and_quotes_are_skipped():
    # These render as plain text and receive no prompt mark, so the outline
    # must not list them or index and mark order would drift apart.
    assert outline(parse("- # Nested\n\n> ## Quoted\n")) == []


def test_document_without_headings():
    assert outline(parse("Just a paragraph.\n")) == []


def test_match_returns_positions_of_a_subsequence():
    assert match("isl", "Installation") == (0, 3, 5)


def test_match_is_case_insensitive():
    assert match("INS", "Installation") == (0, 1, 2)


def test_match_rejects_a_non_subsequence():
    assert match("zz", "Installation") is None


def test_empty_query_matches_everything_with_no_highlights():
    assert match("", "Installation") == ()


def test_filtered_keeps_document_order():
    sections = outline(parse("## Install\n\n## Configure\n\n## Inspect\n"))
    assert [section.title for section, _ in filtered(sections, "ins")] == ["Install", "Inspect"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outline.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lime.outline'`

- [ ] **Step 3: Write the implementation**

```python
# src/lime/outline.py
"""Document headings as a navigable model, independent of any terminal."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_it.token import Token

from lime.render import heading_text


@dataclass(frozen=True)
class Section:
    title: str
    level: int
    index: int
    line: int


def outline(tokens: list[Token]) -> list[Section]:
    """Top-level headings only: exactly those that receive a prompt mark."""
    sections = []
    for position, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0:
            sections.append(
                Section(
                    heading_text(tokens[position + 1]),
                    int(token.tag[1]),
                    len(sections),
                    token.map[0] if token.map else 0,
                )
            )
    return sections


def match(query: str, title: str) -> tuple[int, ...] | None:
    """Character positions of a case-insensitive subsequence, or None."""
    positions = []
    haystack = title.casefold()
    start = 0
    for character in query.casefold():
        found = haystack.find(character, start)
        if found < 0:
            return None
        positions.append(found)
        start = found + 1
    return tuple(positions)


def filtered(
    sections: list[Section], query: str
) -> list[tuple[Section, tuple[int, ...]]]:
    results = []
    for section in sections:
        positions = match(query, section.title)
        if positions is not None:
            results.append((section, positions))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_outline.py -v && uv run ruff check src tests`
Expected: 9 passed, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/lime/outline.py tests/test_outline.py
git commit -m "feat: outline model and subsequence filter"
```

---

### Task 2: Prompt marks during rendering

`render()` grows a return value and emits one mark per top-level heading. This task alone delivers ⌘↑/⌘↓ section navigation that survives lime exiting.

**Files:**
- Modify: `src/lime/render.py` (the `render` function, `render.py:151-251`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `lime.outline.outline`, `lime.outline.Section`
- Produces: `render(...) -> list[Section]`; new keyword-only parameter `marks: bool = False`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render.py`:

```python
from lime.outline import Section

MARK = "\x1b]133;A\x1b\\"


def test_render_returns_the_outline():
    console = Console(file=io.StringIO(), width=40)
    sections = render("# One\n\n## Two\n", console, base=Path.cwd())
    assert sections == [Section("One", 1, 0, 0), Section("Two", 2, 1, 2)]


def test_marks_precede_every_top_level_heading():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    render("# One\n\ntext\n\n## Two\n", console, base=Path.cwd(), marks=True)
    output = stream.getvalue()
    assert output.count(MARK) == 2
    assert output.index(MARK) < output.index("One")
    assert output.index(MARK, output.index("One")) < output.index("Two")


def test_no_marks_unless_requested():
    stream = io.StringIO()
    console = Console(file=stream, width=40)
    render("# One\n", console, base=Path.cwd())
    assert MARK not in stream.getvalue()


def test_nested_headings_get_no_marks_and_no_outline_entry():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    sections = render("- # Nested\n", console, base=Path.cwd(), marks=True)
    assert sections == [] and MARK not in stream.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL, `TypeError: render() got an unexpected keyword argument 'marks'`

- [ ] **Step 3: Modify `render`**

Add the import at the top of `src/lime/render.py`, beside the existing `from lime.graphics import Headings`:

```python
from lime.outline import Section, outline
```

Change the signature to accept `marks` and return sections. Replace the `def render(` signature block:

```python
def render(
    source: str,
    console: Console,
    *,
    base: Path,
    margin: int = 0,
    headings: Headings | None = None,
    heading_labels: bool = True,
    line_numbers: bool = True,
    marks: bool = False,
    mermaid=None,
    images=None,
) -> list[Section]:
```

Immediately after `tokens = document.parsed`, add:

```python
    sections = outline(tokens)
```

Inside the `while index < len(tokens):` loop, as the **first** statement after `token = tokens[index]`, add the mark emission. It must run for image and text headings alike, before either branch prints anything:

```python
        if marks and token.type == "heading_open" and token.level == 0:
            # Ghostty records a prompt mark here, so its own jump_to_prompt
            # walks headings. The predicate must match outline() exactly.
            emit(batch)
            batch = []
            console.file.write("\x1b]133;A\x1b\\")
```

Replace the final `emit(batch)` at the end of the function with:

```python
    emit(batch)
    return sections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v && uv run ruff check src tests`
Expected: all pass, including every pre-existing test

- [ ] **Step 5: Wire marks into the CLI**

In `src/lime/cli.py`, inside the `render(` call, add `marks=terminal.is_tty and not plain,` beside `heading_labels=heading_labels,`. Change the assignment to capture the result:

```python
        sections = render(
```

Add `del sections  # consumed by the reader in a later task` directly after the `render(...)` call closes, so ruff does not flag the unused name.

- [ ] **Step 6: Verify manually in Ghostty, then commit**

Run: `uv run lime README.md`, then press ⌘↑ and ⌘↓.
Expected: the viewport jumps heading to heading. **If it does not, stop** — measurement 1 in the spec has failed and Task 5 must switch to `scroll_to_row`.

```bash
git add src/lime/render.py src/lime/cli.py tests/test_render.py
git commit -m "feat: emit OSC 133 prompt marks at headings"
```

---

### Task 3: Raw-mode input and key decoding

Generalises the `termios` pattern already proven in `terminal.py:36`.

**Files:**
- Create: `src/lime/keys.py`
- Test: `tests/test_keys.py`

**Interfaces:**
- Produces:
  - `Key` str-enum: `UP`, `DOWN`, `ENTER`, `ESCAPE`, `BACKSPACE`, `INTERRUPT`, `EOF`, `NEXT`, `PREVIOUS`
  - `decode(data: bytes) -> tuple[Key | str | None, bytes]` — event and unconsumed remainder
  - `raw_mode(fd: int)` context manager
  - `read_key(fd: int, timeout: float = 0.05) -> Key | str | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keys.py
import os
import termios

import pytest

from lime.keys import Key, decode, raw_mode
from tests.test_terminal import assert_restored


@pytest.mark.parametrize(
    ("data", "event"),
    [
        (b"\x1b[A", Key.UP),
        (b"\x1b[B", Key.DOWN),
        (b"\x1bOA", Key.UP),
        (b"\x1bOB", Key.DOWN),
        (b"\r", Key.ENTER),
        (b"\n", Key.ENTER),
        (b"\x7f", Key.BACKSPACE),
        (b"\x08", Key.BACKSPACE),
        (b"\x03", Key.INTERRUPT),
        (b"\x04", Key.EOF),
        (b"\x0e", Key.NEXT),
        (b"\x10", Key.PREVIOUS),
        (b"t", "t"),
        (b"?", "?"),
    ],
)
def test_decodes_single_events(data, event):
    assert decode(data) == (event, b"")


def test_lone_escape_is_escape():
    assert decode(b"\x1b") == (Key.ESCAPE, b"")


def test_multibyte_utf8_decodes_as_one_character():
    assert decode("é".encode()) == ("é", b"")


def test_incomplete_utf8_is_held_for_more_bytes():
    assert decode("é".encode()[:1]) == (None, "é".encode()[:1])


def test_incomplete_escape_sequence_is_held():
    assert decode(b"\x1b[") == (None, b"\x1b[")


def test_remainder_is_returned_unconsumed():
    assert decode(b"t?") == ("t", b"?")


def test_unknown_escape_sequence_is_dropped():
    assert decode(b"\x1b[3~x") == (None, b"x")


def test_raw_mode_restores_terminal_attributes():
    master, slave = os.openpty()
    original = termios.tcgetattr(slave)
    try:
        with raw_mode(slave):
            assert not termios.tcgetattr(slave)[3] & termios.ICANON
        assert_restored(slave, original)
    finally:
        os.close(master)
        os.close(slave)


def test_raw_mode_restores_on_exception():
    master, slave = os.openpty()
    original = termios.tcgetattr(slave)
    try:
        with pytest.raises(RuntimeError), raw_mode(slave):
            raise RuntimeError("boom")
        assert_restored(slave, original)
    finally:
        os.close(master)
        os.close(slave)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_keys.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lime.keys'`

- [ ] **Step 3: Write the implementation**

```python
# src/lime/keys.py
"""Raw keyboard input from the controlling terminal, never from stdin."""

from __future__ import annotations

import os
import select
import termios
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum

CONTROLS = {
    b"\r": "ENTER",
    b"\n": "ENTER",
    b"\x7f": "BACKSPACE",
    b"\x08": "BACKSPACE",
    b"\x03": "INTERRUPT",
    b"\x04": "EOF",
    b"\x0e": "NEXT",
    b"\x10": "PREVIOUS",
}
SEQUENCES = {b"\x1b[A": "UP", b"\x1b[B": "DOWN", b"\x1bOA": "UP", b"\x1bOB": "DOWN"}


class Key(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    ENTER = "ENTER"
    ESCAPE = "ESCAPE"
    BACKSPACE = "BACKSPACE"
    INTERRUPT = "INTERRUPT"
    EOF = "EOF"
    NEXT = "NEXT"
    PREVIOUS = "PREVIOUS"


def decode(data: bytes) -> tuple[Key | str | None, bytes]:
    """One event off the front of the buffer, with whatever is left over.

    A None event with a non-empty remainder means the bytes are a prefix and
    the caller should read more; None with an empty remainder means nothing.
    """
    if not data:
        return None, b""
    if data[:1] == b"\x1b":
        if len(data) == 1:
            return Key.ESCAPE, b""
        for sequence, name in SEQUENCES.items():
            if data.startswith(sequence):
                return Key[name], data[len(sequence) :]
        if data[1:2] in b"[O":
            # A CSI/SS3 sequence terminates on its first byte in @-~.
            for position in range(2, len(data)):
                if 0x40 <= data[position] <= 0x7E:
                    return None, data[position + 1 :]
            return None, data
        return Key.ESCAPE, data[1:]
    if data[:1] in CONTROLS:
        return Key[CONTROLS[data[:1]]], data[1:]
    for length in range(1, min(4, len(data)) + 1):
        try:
            return data[:length].decode(), data[length:]
        except UnicodeDecodeError:
            continue
    return (None, data) if len(data) < 4 else (None, data[1:])


@contextmanager
def raw_mode(fd: int) -> Iterator[None]:
    original = termios.tcgetattr(fd)
    settings = termios.tcgetattr(fd)
    settings[3] &= ~(termios.ICANON | termios.ECHO)
    settings[6][termios.VMIN] = 0
    settings[6][termios.VTIME] = 0
    try:
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)


def read_key(fd: int, timeout: float = 0.05) -> Key | str | None:
    """Block for one event; a partial sequence gets one short extra read."""
    buffer = b""
    while True:
        if not select.select([fd], [], [], None if not buffer else timeout)[0]:
            return decode(buffer)[0] if buffer else None
        chunk = os.read(fd, 64)
        if not chunk:
            return None
        buffer += chunk
        event, remainder = decode(buffer)
        if event is not None:
            return event
        if remainder == buffer:
            continue
        buffer = remainder
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_keys.py -v && uv run ruff check src tests`
Expected: 20 passed, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/lime/keys.py tests/test_keys.py
git commit -m "feat: raw-mode input and key decoding"
```

---

### Task 4: Overlay rendering

Pure string generation, so the exact bytes are asserted in tests. Overlays reserve fresh rows and never cover printed content.

**Files:**
- Create: `src/lime/overlay.py`
- Test: `tests/test_overlay.py`

**Interfaces:**
- Consumes: `lime.outline.Section`
- Produces:
  - `MINIMUM = 6`
  - `height(rows: int) -> int` — 0 when too short
  - `reserve(rows: int) -> str`, `erase() -> str`
  - `picker(entries, selected, query, width, rows) -> str`
  - `help_text(width, rows, jump: str | None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overlay.py
from lime.outline import Section, filtered
from lime.overlay import MINIMUM, erase, height, help_text, picker, reserve

SECTIONS = [Section("Installation", 2, 0, 0), Section("Install with Homebrew", 3, 1, 4)]


def test_height_is_capped_and_floored():
    assert height(40) == 12
    assert height(14) == 10
    assert height(10) == 6
    assert height(9) == 0


def test_minimum_is_six():
    assert MINIMUM == 6


def test_reserve_scrolls_then_returns_to_the_top_of_the_block():
    assert reserve(6) == "\n" * 6 + "\x1b[6A"


def test_erase_clears_to_end_of_screen():
    assert erase() == "\r\x1b[J"


def test_picker_is_wrapped_in_synchronized_output():
    output = picker(filtered(SECTIONS, ""), 0, "", 50, 40)
    assert output.startswith("\x1b[?2026h") and output.endswith("\x1b[?2026l")


def test_picker_shows_titles_levels_and_query():
    output = picker(filtered(SECTIONS, "ins"), 0, "ins", 50, 40)
    assert "ins" in output and "Installation" in output and "H2" in output and "H3" in output


def test_picker_marks_the_selected_entry():
    first = picker(filtered(SECTIONS, ""), 0, "", 50, 40)
    second = picker(filtered(SECTIONS, ""), 1, "", 50, 40)
    assert first.index("›") < first.index("Installation")
    assert second.index("›") > second.index("Installation")


def test_picker_indents_by_heading_level():
    output = picker(filtered(SECTIONS, ""), 0, "", 50, 40)
    lines = output.splitlines()
    deep = next(line for line in lines if "Homebrew" in line)
    shallow = next(line for line in lines if "Installation" in line)
    assert deep.index("Install with") > shallow.index("Installation")


def test_picker_reports_an_empty_result():
    assert "No matching" in picker([], 0, "zzz", 50, 40)


def test_picker_reports_a_document_without_headings():
    assert "No headings" in picker([], 0, "", 50, 40)


def test_picker_returns_nothing_when_the_window_is_too_short():
    assert picker(filtered(SECTIONS, ""), 0, "", 50, 9) == ""


def test_picker_never_exceeds_its_height():
    many = [Section(f"Section {n}", 2, n, n) for n in range(50)]
    output = picker(filtered(many, ""), 0, "", 50, 40)
    assert len(output.splitlines()) <= height(40)


def test_picker_scrolls_to_keep_the_selection_visible():
    many = [Section(f"Section {n}", 2, n, n) for n in range(50)]
    assert "Section 49" in picker(filtered(many, ""), 49, "", 50, 40)


def test_help_lists_lime_and_ghostty_keys():
    output = help_text(60, 40, None)
    for key in ("t", "?", "q", "n", "p", "g", "G"):
        assert key in output
    assert "⌘F" in output


def test_help_explains_a_disabled_jump():
    assert "reprint" in help_text(60, 40, "reprint")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_overlay.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lime.overlay'`

- [ ] **Step 3: Write the implementation**

```python
# src/lime/overlay.py
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
    available = rows - 4
    return 0 if available < MINIMUM else min(12, available)


def reserve(rows: int) -> str:
    return "\n" * rows + f"\x1b[{rows}A"


def erase() -> str:
    return "\r\x1b[J"


def frame(lines: list[str], rows: int) -> str:
    """Pad to exactly rows lines and return the cursor to the block's top."""
    lines = [*lines, *[""] * (rows - len(lines))][:rows]
    body = "\r\n".join(line + "\x1b[K" for line in lines)
    return f"{SYNC_START}\r{body}\r\x1b[{rows - 1}A{SYNC_END}"


def picker(
    entries: list[tuple[Section, tuple[int, ...]]],
    selected: int,
    query: str,
    width: int,
    rows: int,
) -> str:
    total = height(rows)
    if not total:
        return ""
    inner = max(20, width - 4)
    lines = [
        "╭─ Jump to section " + "─" * max(0, inner - 17) + "╮",
        f"│ > {query}" + " " * max(0, inner - len(query) - 3) + "│",
        "├" + "─" * inner + "┤",
    ]
    body = total - 4
    if not entries:
        message = "No matching sections" if query else "No headings in this document"
        lines.append("│ " + message.ljust(inner - 1) + "│")
    else:
        start = max(0, min(selected - body + 1, len(entries) - body))
        for position, (section, _) in enumerate(entries[max(0, start) : max(0, start) + body]):
            index = position + max(0, start)
            marker = "›" if index == selected else " "
            indent = "  " * (section.level - 1)
            label = f"H{section.level}"
            title = f"{marker} {indent}{section.title}"
            room = inner - len(label) - 2
            title = title[: room - 1] + "…" if len(title) > room else title
            lines.append("│" + title.ljust(room) + label + " │")
    lines.append(
        "╰─ ↑/↓ move · Enter jump · Esc close "
        + "─" * max(0, inner - 31)
        + "╯"
    )
    return frame(lines, total)


def help_text(width: int, rows: int, jump: str | None) -> str:
    total = height(rows)
    if not total:
        return ""
    inner = max(20, width - 4)
    lines = ["╭─ Keys " + "─" * max(0, inner - 6) + "╮"]
    for key, action, native, native_action in KEYS:
        left = f"{key:<10}{action:<26}"
        right = f"{native} {native_action}" if native else ""
        lines.append("│ " + (left + right)[: inner - 1].ljust(inner - 1) + "│")
    if jump:
        note = f"Jumping uses {jump} in this session."
        lines.append("│ " + note[: inner - 1].ljust(inner - 1) + "│")
    lines.append("╰" + "─" * inner + "╯")
    return frame(lines, total)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_overlay.py -v && uv run ruff check src tests`
Expected: 16 passed, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/lime/overlay.py tests/test_overlay.py
git commit -m "feat: table of contents and help overlays"
```

---

### Task 5: Ghostty bridge

Surface discovery by title nonce, a persistent `osascript` helper, and the anchored jump. The subprocess boundary is injected so tests never spawn anything.

**Files:**
- Create: `src/lime/ghostty.py`
- Test: `tests/test_ghostty.py`

**Interfaces:**
- Produces:
  - `title(text: str) -> str` — OSC 2 sequence
  - `jump_actions(index: int, total: int) -> list[str]`
  - `Bridge(runner=None)` with `discover(stream) -> bool`, `perform(action) -> bool`, `jump(index, total) -> bool`, `available -> bool`, `close()`
  - `RUNNER_UNAVAILABLE` sentinel reason string `"reprint"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ghostty.py
import io

from lime.ghostty import Bridge, jump_actions, title


def test_title_is_an_osc_2_sequence():
    assert title("lime · README.md") == "\x1b]2;lime · README.md\x1b\\"


def test_title_strips_control_characters():
    assert title("a\x1bb\nc") == "\x1b]2;abc\x1b\\"


def test_jump_reanchors_then_walks_back_from_the_end():
    assert jump_actions(0, 8) == ["scroll_to_bottom", "jump_to_prompt:-7"]
    assert jump_actions(7, 8) == ["scroll_to_bottom", "jump_to_prompt:0"]
    assert jump_actions(5, 8) == ["scroll_to_bottom", "jump_to_prompt:-2"]


class FakeRunner:
    def __init__(self, replies):
        self.replies = list(replies)
        self.scripts = []

    def __call__(self, script):
        self.scripts.append(script)
        return self.replies.pop(0) if self.replies else "false"

    def close(self):
        self.closed = True


def test_discovery_sets_a_nonce_then_restores_the_title():
    stream = io.StringIO()
    runner = FakeRunner(["SURFACE-1"])
    bridge = Bridge(runner)
    assert bridge.discover(stream) is True
    assert bridge.available
    written = stream.getvalue()
    assert written.startswith("\x1b]2;lime-") and written.count("\x1b]2;") == 2
    assert "SURFACE-1" not in written


def test_discovery_failure_disables_jumping():
    bridge = Bridge(FakeRunner([""]))
    assert bridge.discover(io.StringIO()) is False
    assert not bridge.available


def test_jump_performs_both_actions_against_the_surface():
    runner = FakeRunner(["SURFACE-1", "true", "true"])
    bridge = Bridge(runner)
    bridge.discover(io.StringIO())
    assert bridge.jump(2, 5) is True
    assert "scroll_to_bottom" in runner.scripts[1]
    assert "jump_to_prompt:-2" in runner.scripts[2]
    assert "SURFACE-1" in runner.scripts[2]


def test_a_failed_action_disables_the_bridge_permanently():
    runner = FakeRunner(["SURFACE-1", "false"])
    bridge = Bridge(runner)
    bridge.discover(io.StringIO())
    assert bridge.jump(1, 5) is False
    assert not bridge.available
    assert bridge.jump(2, 5) is False
    assert len(runner.scripts) == 2  # no retry after the failure


def test_jump_without_discovery_is_false():
    assert Bridge(FakeRunner([])).jump(0, 3) is False


def test_close_is_safe_before_discovery():
    Bridge(FakeRunner([])).close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ghostty.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lime.ghostty'`

- [ ] **Step 3: Write the implementation**

```python
# src/lime/ghostty.py
"""Ask Ghostty to move its own viewport; never move it from this process."""

from __future__ import annotations

import secrets
import shutil
import subprocess
from typing import TextIO

from lime.terminal import clean_text

FIND = """
tell application "Ghostty"
  repeat with w in windows
    repeat with t in tabs of w
      set s to focused terminal of t
      if name of s is "{nonce}" then return id of s
    end repeat
  end repeat
end tell
return ""
"""
ACT = """
tell application "Ghostty"
  set s to first terminal whose id is "{surface}"
  return perform action "{action}" on s
end tell
"""


def title(text: str) -> str:
    return f"\x1b]2;{clean_text(text).replace(chr(10), '')}\x1b\\"


def jump_actions(index: int, total: int) -> list[str]:
    """Anchor at the bottom so the walk back is independent of manual scrolling."""
    return ["scroll_to_bottom", f"jump_to_prompt:-{total - index - 1}"]


def osascript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


class Bridge:
    def __init__(self, runner=None) -> None:
        self.runner = runner or (osascript if shutil.which("osascript") else None)
        self.surface: str | None = None
        self.failed = False

    @property
    def available(self) -> bool:
        return bool(self.surface) and not self.failed

    def discover(self, stream: TextIO) -> bool:
        """Name this surface uniquely, find it by that name, then rename it."""
        if self.runner is None:
            return False
        nonce = f"lime-{secrets.token_hex(8)}"
        stream.write(title(nonce))
        stream.flush()
        surface = self.runner(FIND.format(nonce=nonce))
        stream.write(title("lime"))
        stream.flush()
        self.surface = surface or None
        return self.available

    def perform(self, action: str) -> bool:
        if not self.available:
            return False
        if self.runner(ACT.format(surface=self.surface, action=action)) != "true":
            # One bad action means the bridge is gone; retrying every keypress
            # would feel broken, so fall back for the rest of the session.
            self.failed = True
            return False
        return True

    def jump(self, index: int, total: int) -> bool:
        return all(self.perform(action) for action in jump_actions(index, total))

    def close(self) -> None:
        if self.runner is not None and hasattr(self.runner, "close"):
            self.runner.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ghostty.py -v && uv run ruff check src tests`
Expected: 8 passed, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/lime/ghostty.py tests/test_ghostty.py
git commit -m "feat: Ghostty action bridge with surface discovery"
```

---

### Task 6: Reader state machine

The decision logic is a pure function so the whole interaction is tested without a terminal.

**Files:**
- Create: `src/lime/reader.py`
- Test: `tests/test_reader.py`

**Interfaces:**
- Consumes: `Key`, `Section`, `filtered`, `picker`, `help_text`, `Bridge`
- Produces:
  - `State(mode: str, query: str, selected: int, current: int | None)` frozen dataclass, `mode` in `{"idle", "toc", "help"}`
  - `Action` str-enum: `QUIT`, `JUMP`, `TOP`, `BOTTOM`, `REDRAW`, `OPEN`, `CLOSE`
  - `step(state, key, sections) -> tuple[State, Action | None]`
  - `bar(name: str, sections, current) -> str`
  - `run(...) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reader.py
from lime.keys import Key
from lime.outline import Section
from lime.reader import Action, State, bar, step

SECTIONS = [
    Section("Install", 2, 0, 0),
    Section("Configure", 2, 1, 4),
    Section("Inspect", 2, 2, 8),
]
IDLE = State("idle", "", 0, None)


def test_t_opens_the_table_of_contents():
    state, action = step(IDLE, "t", SECTIONS)
    assert state.mode == "toc" and action is Action.OPEN


def test_question_mark_toggles_help():
    state, _ = step(IDLE, "?", SECTIONS)
    assert state.mode == "help"
    assert step(state, "?", SECTIONS)[0].mode == "idle"


def test_q_quits_from_idle():
    assert step(IDLE, "q", SECTIONS)[1] is Action.QUIT


def test_interrupt_quits():
    assert step(IDLE, Key.INTERRUPT, SECTIONS)[1] is Action.QUIT


def test_g_and_shift_g_scroll():
    assert step(IDLE, "g", SECTIONS)[1] is Action.TOP
    assert step(IDLE, "G", SECTIONS)[1] is Action.BOTTOM


def test_n_and_p_walk_sections_and_track_position():
    state, action = step(IDLE, "n", SECTIONS)
    assert action is Action.JUMP and state.current == 0
    state, _ = step(state, "n", SECTIONS)
    assert state.current == 1
    state, _ = step(state, "p", SECTIONS)
    assert state.current == 0


def test_n_stops_at_the_last_section():
    state = State("idle", "", 0, 2)
    assert step(state, "n", SECTIONS)[0].current == 2


def test_p_stops_at_the_first_section():
    state = State("idle", "", 0, 0)
    assert step(state, "p", SECTIONS)[0].current == 0


def test_typing_in_the_picker_filters_rather_than_binding_keys():
    state = State("toc", "", 0, None)
    for character in "ins":
        state, _ = step(state, character, SECTIONS)
    assert state.query == "ins" and state.mode == "toc"


def test_backspace_edits_the_query():
    state = State("toc", "ins", 0, None)
    assert step(state, Key.BACKSPACE, SECTIONS)[0].query == "in"


def test_arrows_move_the_selection_and_wrap():
    state = State("toc", "", 0, None)
    assert step(state, Key.DOWN, SECTIONS)[0].selected == 1
    assert step(state, Key.UP, SECTIONS)[0].selected == 2


def test_ctrl_n_and_ctrl_p_also_move_the_selection():
    state = State("toc", "", 0, None)
    assert step(state, Key.NEXT, SECTIONS)[0].selected == 1
    assert step(state, Key.PREVIOUS, SECTIONS)[0].selected == 2


def test_selection_is_clamped_when_the_filter_shrinks_results():
    state = State("toc", "", 2, None)
    state, _ = step(state, "z", SECTIONS)  # matches nothing
    assert state.selected == 0


def test_enter_jumps_to_the_filtered_selection_and_closes():
    state = State("toc", "ins", 1, None)  # Install, Inspect
    state, action = step(state, Key.ENTER, SECTIONS)
    assert action is Action.JUMP and state.current == 2 and state.mode == "idle"


def test_enter_with_no_matches_does_nothing():
    state = State("toc", "zzz", 0, None)
    assert step(state, Key.ENTER, SECTIONS)[1] is None


def test_escape_closes_without_moving():
    state = State("toc", "ins", 1, 0)
    state, action = step(state, Key.ESCAPE, SECTIONS)
    assert state.mode == "idle" and state.current == 0 and action is Action.CLOSE


def test_q_closes_the_picker_instead_of_quitting():
    # q must be filter text inside the picker, not a quit key.
    state = State("toc", "", 0, None)
    assert step(state, "q", SECTIONS)[0].query == "q"


def test_bar_omits_the_section_before_any_jump():
    assert bar("README.md", SECTIONS, None) == "lime · README.md · ? for keys"


def test_bar_names_the_last_jumped_section():
    assert bar("README.md", SECTIONS, 1) == "lime · README.md · Configure 2/3 · ? for keys"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reader.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lime.reader'`

- [ ] **Step 3: Write the state machine and loop**

```python
# src/lime/reader.py
"""Stay resident for keys while Ghostty keeps scrolling, selection and search."""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TextIO

from lime.ghostty import Bridge, title
from lime.keys import Key, raw_mode, read_key
from lime.outline import Section, filtered
from lime.overlay import erase, height, help_text, picker, reserve


class Action(StrEnum):
    QUIT = "QUIT"
    JUMP = "JUMP"
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    REDRAW = "REDRAW"
    OPEN = "OPEN"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class State:
    mode: str = "idle"
    query: str = ""
    selected: int = 0
    current: int | None = None


def bar(name: str, sections: list[Section], current: int | None) -> str:
    if current is None or not sections:
        return f"lime · {name} · ? for keys"
    section = sections[current]
    return f"lime · {name} · {section.title} {current + 1}/{len(sections)} · ? for keys"


def step(state: State, key: Key | str, sections: list[Section]) -> tuple[State, Action | None]:
    if state.mode == "toc":
        return picker_step(state, key, sections)
    if state.mode == "help":
        if key in {"?", "q", Key.ESCAPE, Key.INTERRUPT}:
            return replace(state, mode="idle"), Action.CLOSE
        return state, None
    if key in {"q", Key.INTERRUPT, Key.EOF}:
        return state, Action.QUIT
    if key == "?":
        return replace(state, mode="help"), Action.OPEN
    if key == "t":
        return replace(state, mode="toc", query="", selected=0), Action.OPEN
    if key == "g":
        return state, Action.TOP
    if key == "G":
        return state, Action.BOTTOM
    if key in {"n", "p"} and sections:
        if state.current is None:
            target = 0
        else:
            target = state.current + (1 if key == "n" else -1)
        target = max(0, min(target, len(sections) - 1))
        return replace(state, current=target), Action.JUMP
    return state, None


def picker_step(state: State, key: Key | str, sections: list[Section]) -> tuple[State, Action | None]:
    entries = filtered(sections, state.query)
    if key in {Key.ESCAPE, Key.INTERRUPT}:
        return replace(state, mode="idle"), Action.CLOSE
    if key is Key.ENTER:
        if not entries:
            return state, None
        chosen = entries[min(state.selected, len(entries) - 1)][0]
        return replace(state, mode="idle", current=chosen.index), Action.JUMP
    if key in {Key.DOWN, Key.NEXT} and entries:
        return replace(state, selected=(state.selected + 1) % len(entries)), Action.REDRAW
    if key in {Key.UP, Key.PREVIOUS} and entries:
        return replace(state, selected=(state.selected - 1) % len(entries)), Action.REDRAW
    if key is Key.BACKSPACE:
        return replace(state, query=state.query[:-1], selected=0), Action.REDRAW
    if isinstance(key, str) and len(key) == 1 and key.isprintable():
        return replace(state, query=state.query + key, selected=0), Action.REDRAW
    return state, None


def draw(stream: TextIO, state: State, sections: list[Section], columns: int, rows: int, jump: str | None) -> None:
    if state.mode == "toc":
        stream.write(picker(filtered(sections, state.query), state.selected, state.query, columns, rows))
    elif state.mode == "help":
        stream.write(help_text(columns, rows, jump))
    stream.flush()


def run(stream: TextIO, sections: list[Section], name: str, terminal, mode: str) -> int:
    """Read keys until the user quits, restoring the terminal on every exit."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return 0
    bridge = Bridge()
    if mode != "reprint":
        bridge.discover(stream)
    jump = None if bridge.available else "reprint"
    state = State()
    size = [terminal.columns, terminal.rows]

    def resized(*_):
        updated = os.get_terminal_size(fd)
        size[:] = [updated.columns, updated.lines]
        if state.mode != "idle":
            draw(stream, state, sections, size[0], size[1], jump)

    previous = signal.signal(signal.SIGWINCH, resized)
    try:
        with raw_mode(fd):
            stream.write(title(bar(name, sections, state.current)))
            stream.flush()
            while True:
                key = read_key(fd)
                if key is None:
                    continue
                opened = state.mode
                state, action = step(state, key, sections)
                if action is Action.QUIT:
                    return 0
                if action is Action.OPEN:
                    if not height(size[1]):
                        stream.write("\r\nWindow too short for the overlay.\r\n")
                        stream.flush()
                        state = replace(state, mode="idle")
                        continue
                    stream.write(reserve(height(size[1])))
                if action is Action.CLOSE or (action is Action.JUMP and opened == "toc"):
                    stream.write(erase())
                if action in {Action.OPEN, Action.REDRAW}:
                    draw(stream, state, sections, size[0], size[1], jump)
                if action is Action.TOP:
                    bridge.perform("scroll_to_top")
                if action is Action.BOTTOM:
                    bridge.perform("scroll_to_bottom")
                if action is Action.JUMP and state.current is not None:
                    if not bridge.jump(state.current, len(sections)):
                        jump = "reprint"
                    stream.write(title(bar(name, sections, state.current)))
                    stream.flush()
    finally:
        signal.signal(signal.SIGWINCH, previous)
        if state.mode != "idle":
            stream.write(erase())
        stream.write(title(name))
        stream.flush()
        bridge.close()
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reader.py -v && uv run ruff check src tests`
Expected: 19 passed, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/lime/reader.py tests/test_reader.py
git commit -m "feat: resident reader with table of contents and help"
```

---

### Task 7: Configuration and CLI wiring

**Files:**
- Modify: `src/lime/config.py` (`Settings`, and the validation loops)
- Modify: `src/lime/cli.py` (`parser`, `main`)
- Modify: `config.example.yaml`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `lime.reader.run`, `lime.render.render`
- Produces: `Settings.interactive: str = "auto"`, `Settings.jump: str = "auto"`; flags `--interactive` / `--no-interactive`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_interactive_and_jump_default_to_auto():
    assert load_settings().interactive == "auto" and load_settings().jump == "auto"


@pytest.mark.parametrize(
    ("key", "value"), [("interactive", "sometimes"), ("jump", "teleport")]
)
def test_invalid_choice_is_rejected(tmp_path, key, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"{key}: {value}\n")
    with pytest.raises(ValueError, match=key):
        load_settings(path)


def test_valid_choices_are_accepted(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("interactive: 'off'\njump: reprint\n")
    settings = load_settings(path)
    assert settings.interactive == "off" and settings.jump == "reprint"
```

Append to `tests/test_cli.py`:

```python
def test_piped_output_never_enters_the_reader():
    result = run("-", source="# One\n\n## Two\n")
    assert result.returncode == 0 and "\x1b" not in result.stdout


def test_no_interactive_flag_is_accepted(tmp_path):
    document = tmp_path / "doc.md"
    document.write_text("# One\n")
    assert run("--no-interactive", str(document)).returncode == 0


def test_reader_is_skipped_without_a_tty(tmp_path, monkeypatch):
    document = tmp_path / "doc.md"
    document.write_text("# One\n")
    called = []
    monkeypatch.setattr("lime.reader.run", lambda *a, **k: called.append(a) or 0)
    assert main([str(document)]) == 0
    assert not called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -v`
Expected: FAIL, `AttributeError: 'Settings' object has no attribute 'interactive'`

- [ ] **Step 3: Extend the configuration**

In `src/lime/config.py`, add two fields to `Settings` after `images`:

```python
    interactive: str = "auto"
    jump: str = "auto"
```

Extend the existing choice-validation loop to cover them:

```python
    for key, options in (
        ("headings", ("auto", "image", "text")),
        ("mermaid", ("auto", "off")),
        ("interactive", ("auto", "on", "off")),
        ("jump", ("auto", "scroll", "reprint")),
    ):
```

- [ ] **Step 4: Wire the CLI**

In `src/lime/cli.py`, add to `parser()`:

```python
    result.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="stay open for key commands (default: in Ghostty only)",
    )
```

Add the import beside the others:

```python
from lime.reader import run as read
```

In `main()`, capture the outline by changing `render(` to `sections = render(`. After the closing parenthesis of the `render(...)` call and the trailing vertical-padding print, before `return 0`, add:

```python
    interactive = (
        args.interactive
        if args.interactive is not None
        else {"auto": terminal.graphics, "on": True, "off": False}[settings.interactive]
    )
    if interactive and sections and not plain and terminal.is_tty:
        return read(sys.stdout, sections, path.name if args.file != "-" else "stdin", terminal, settings.jump)
    return 0
```

Replace the `del sections` line added in Task 2, since the value is now used.

- [ ] **Step 5: Document the new keys**

Append to `config.example.yaml`:

```yaml
interactive: auto      # auto stays open for keys in Ghostty; on, or off
jump: auto             # auto uses Ghostty's viewport; scroll, or reprint
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v && uv run ruff check src tests scripts`
Expected: all pass, including every pre-existing test

- [ ] **Step 7: Verify by hand in Ghostty**

Run: `uv run lime README.md`
Expected: the document prints, the tab title reads `lime · README.md · ? for keys`, `?` opens help, `t` opens the picker, typing filters, Enter jumps, `q` exits with the title restored and echo working.

- [ ] **Step 8: Commit**

```bash
git add src/lime/config.py src/lime/cli.py config.example.yaml tests/test_config.py tests/test_cli.py
git commit -m "feat: enable the reader in Ghostty with config and flags"
```

---

### Task 8: Documentation

Written for someone opening this project for the first time. Plain English, every term defined on first use.

**Files:**
- Modify: `README.md` (new section before "Configuration and spacing"; update the intro paragraph and the Options block)
- Modify: `docs/ideas.md` (correct the superseded claim)

- [ ] **Step 1: Add the README section**

Insert after the "What v0.1 renders" section:

````markdown
## Navigating a document

Lime prints your document and then stays open, waiting for a key. Ghostty keeps
doing the scrolling, selecting, and searching; lime just tells it where to go.
One sentence to remember: **lime prints and marks, Ghostty scrolls and searches.**

Press **?** at any time to see every key. The window title always shows
`lime · DOC.md · ? for keys`, so the reminder is on screen even when you have
scrolled far up the document.

### Jumping between sections

While printing, lime puts an invisible bookmark just before each heading. These
are called *prompt marks* — a small standard signal (OSC 133) that shells
normally use to tell the terminal where each command started. Ghostty already
knows how to jump between prompt marks, so by leaving one at every heading, lime
gets section navigation using machinery the terminal already has.

The useful part: those bookmarks stay in the scrollback after lime exits.

| Key | What it does |
| --- | --- |
| `t` | Open the table of contents |
| `n` / `p` | Next / previous section |
| `g` / `G` | Jump to the top / bottom |
| `?` | Show all keys |
| `q` | Quit |
| **⌘F** | Search — this is Ghostty's, not lime's |
| **⌘↑ / ⌘↓** | Previous / next section — also Ghostty's, works after lime exits |

In the table of contents, type to filter headings, move with the arrow keys or
Ctrl-N / Ctrl-P, press Enter to jump, and Escape to close. Plain `j` and `k` are
not shortcuts there, because you may be typing them into the filter.

### Two things that will look odd at first

**The bar is in the window title, not on the screen.** A status line drawn at the
bottom of the screen disappears the moment you scroll up, because scrolling up
means looking at history and the bottom line is not part of it. Ghostty draws the
window title no matter where you have scrolled, so that is where the bar lives.

**The title only updates when lime moves you.** The terminal does not tell a
program when you scroll with the trackpad, so lime cannot know where you are
looking. The section in the title is the last one *lime* jumped to. Scroll by
hand and it will be out of date until you jump again. Before your first jump the
section is left out rather than guessed.

### When jumping is not available

Moving Ghostty's viewport uses macOS automation, so it is macOS-only. Elsewhere —
and if lime cannot work out which terminal window it is running in — selecting a
heading reprints that section at the bottom instead of scrolling to it. You end up
in the same place; the document just gets longer. Set `jump: reprint` in your
config to always use this. The help screen tells you which one is active.

Lime stays open only when it detects Ghostty and is not being piped or
redirected. `lime DOC.md > out.txt` and `cat DOC.md | lime -` behave exactly as
before. Use `--no-interactive` to print and exit, or `--interactive` to force the
reader on.
````

- [ ] **Step 2: Update the README intro and Options**

In the intro paragraph, replace "It doesn't launch a pager or capture your mouse and navigation keys." with:

```markdown
It doesn't launch a pager or capture your mouse and scrolling keys. In Ghostty it
stays open for a few navigation keys — press **?** to see them, **q** to leave.
```

Add to the Options block:

```sh
lime --no-interactive DOC.md           # Print and return to the shell immediately
```

- [ ] **Step 3: Correct `docs/ideas.md`**

Replace the paragraph beginning "The picker is straightforward; moving to the selected heading" with:

```markdown
**Implemented.** See `docs/superpowers/specs/2026-09-09-interactive-navigation-design.md`.
The claim that terminal escape sequences offer no arbitrary-row jump into
Ghostty's scrollback was wrong for Ghostty 1.3.1: `ghostty +list-actions` lists
`scroll_to_row` and `jump_to_prompt`, and the AppleScript `perform action`
command takes any action string against a terminal surface. Lime marks headings
with OSC 133 and asks Ghostty to move its own viewport, so native scrollback and
search are kept.
```

Delete the same section's opening sentence "This belongs to a persistent interactive mode: after v0.1 returns to the shell, `t` is shell input." and the sentence proposing `lime --read DOC.md`, which is now `--interactive`.

- [ ] **Step 4: Check the documented keys against the code**

Run: `uv run pytest -v && uv run ruff check src tests scripts`
Expected: all pass. Confirm by eye that every key in the README table appears in `overlay.KEYS` and in `reader.step`.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ideas.md
git commit -m "docs: explain interactive navigation for new readers"
```

---

## Self-Review

**Spec coverage.** Layer 1 → Task 2. Layer 2 → Tasks 3 and 6. Layer 3 → Task 5. Surface discovery → Task 5. Latency budget → Task 5 (`Bridge.close`, injected runner; the persistent helper is a `runner` swap needing no interface change). Reprint fallback → Tasks 5 and 6 via the `jump` setting. Control bar in the title → Task 6 `bar` and `ghostty.title`. Tracking limitation → Tasks 6 and 8. Overlays → Task 4. TOC behaviour → Tasks 4 and 6. Help modal → Tasks 4 and 6. Key bindings → Task 6. Mode selection → Task 7. Components table → Tasks 1–7. Error handling → Tasks 5, 6, 7. Testing → every task. Documentation → Task 8. Measurement 1 is a gate in Task 2 Step 6; measurement 2 is the `jump` setting's default.

**Placeholder scan.** No TBD, TODO, or "similar to Task N". Every code step carries the code.

**Type consistency.** `Section(title, level, index, line)` is constructed in Task 1 and consumed identically in 2, 4, 6. `filtered` returns `list[tuple[Section, tuple[int, ...]]]` in Task 1 and is unpacked that way in Tasks 4 and 6. `Key` members used in Task 6 are all defined in Task 3. `height`, `reserve`, `erase`, `picker`, `help_text` signatures match between Tasks 4 and 6. `Bridge.jump(index, total)` matches its call in Task 6. `render(..., marks=...) -> list[Section]` matches its call in Task 7.

**One known deviation.** The spec's persistent `osascript` process is not built in Task 5; a per-call `subprocess.run` is, behind the injected `runner` seam. If the 50 ms budget fails in Task 7 Step 7, swapping in a long-lived runner is a change to one class with no caller impact. Measure before building it.
