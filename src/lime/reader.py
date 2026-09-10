"""Stay resident for keys while Ghostty keeps scrolling, selection and search."""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TextIO

from lime.ghostty import END, START, Bridge, _debug, title
from lime.keys import Key, KeyReader, raw_mode
from lime.outline import Section, search
from lime.overlay import ENTER, LEAVE, contents, shortcuts
from lime.terminal import sync

# A burst of SIGWINCHs (a drag-resize) coalesces into one reflow this long after
# the last one, so the document is reprinted once rather than on every frame.
REFLOW_DELAY = 0.15

# Ghostty scrolls its own viewport to the bottom a frame or two after a keypress
# (its `scroll-to-bottom = keystroke` default), which lands *after* lime's own
# scroll and undoes it. Re-issuing the scroll a few times over the next ~60ms
# wins that race; the extra Apple Events cost microseconds each.
MOVE_SETTLE = (0.015, 0.035, 0.06)


class Action(StrEnum):
    QUIT = "QUIT"
    JUMP = "JUMP"
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    REDRAW = "REDRAW"
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class Terminated(Exception):
    """Raised from a SIGTERM/SIGHUP handler to unwind the loop and clean up."""


@dataclass(frozen=True)
class Layout:
    """One rendering of the document: its outline plus where the headings landed.

    `anchors` is the output row of the document start followed by each top-level
    heading, so ``len(anchors) == len(sections) + 1``. After a resize reflow the
    reader scrolls to these absolute rows, Ghostty's prompt marks having become
    unreliable once a CSI 3J cleared the scrollback.
    """

    sections: list[Section]
    anchors: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class State:
    mode: str = "idle"
    selected: int = 0
    current: int | None = None
    # The live filter typed into the table of contents; "" while it is unfiltered.
    query: str = ""


def bar(name: str, sections: list[Section], current: int | None) -> str:
    """The window title: document name, plus the last section jumped to, if any."""
    if current is None or not sections:
        return f"lime · {name}"
    section = sections[current]
    return f"lime · {name} · {section.title} {current + 1}/{len(sections)}"


def step(state: State, key: Key | str, sections: list[Section]) -> tuple[State, Action | None]:
    """The whole interaction as one pure decision: next state, plus what to do."""
    if key is Key.EOF:
        return state, Action.QUIT
    if state.mode == "toc":
        return contents_step(state, key, sections)
    if state.mode == "help":
        return replace(state, mode="idle"), Action.CLOSE
    if key in {"q", Key.INTERRUPT, Key.EOF}:
        return state, Action.QUIT
    if key == "?":
        return replace(state, mode="help"), Action.OPEN
    if key == "t":
        # Open on the heading lime last jumped to, so the list starts where the
        # reader is rather than at the top of an unrelated document.
        start = state.current if state.current is not None else 0
        return replace(state, mode="toc", selected=start, query=""), Action.OPEN
    if key == "g":
        return state, Action.TOP
    if key == "G":
        return state, Action.BOTTOM
    if key in {"n", "p"} and sections:
        forward = key == "n"
        if state.current is None:
            # First step is from the opening position (the document start), so
            # forward lands on section 0 and backward has nowhere to go.
            return (replace(state, current=0), Action.JUMP) if forward else (state, None)
        target = state.current + (1 if forward else -1)
        if not 0 <= target < len(sections):
            return state, None
        return replace(state, current=target), Action.JUMP
    return state, None


def contents_step(
    state: State, key: Key | str, sections: list[Section]
) -> tuple[State, Action | None]:
    """Key handling while the contents are open: filter, move the row, choose, close.

    Every printable key narrows the list, so navigation is arrows and Ctrl-N/P
    only; Esc (or Ctrl-C) closes and drops the filter, Enter jumps to the lit
    match.
    """
    matches = search(sections, state.query)
    last = max(len(matches) - 1, 0)
    if key in {Key.ESCAPE, Key.INTERRUPT}:
        return replace(state, mode="idle", query=""), Action.CLOSE
    if key is Key.BACKSPACE:
        if not state.query:
            return state, None
        return replace(state, query=state.query[:-1], selected=0), Action.REDRAW
    if key is Key.ENTER:
        if not matches:
            return replace(state, mode="idle", query=""), Action.CLOSE
        chosen = matches[min(state.selected, last)]
        return replace(state, mode="idle", query="", current=chosen.index), Action.JUMP
    if key in {Key.DOWN, Key.NEXT}:
        return replace(state, selected=min(state.selected + 1, last)), Action.REDRAW
    if key in {Key.UP, Key.PREVIOUS}:
        return replace(state, selected=max(state.selected - 1, 0)), Action.REDRAW
    if isinstance(key, str) and len(key) == 1 and key.isprintable():
        return replace(state, query=state.query + key, selected=0), Action.REDRAW
    return state, None


def draw(
    stream: TextIO,
    state: State,
    sections: list[Section],
    columns: int,
    rows: int,
) -> None:
    """Render the contents across the whole alternate screen."""
    if state.mode == "toc":
        stream.write(contents(sections, state.selected, columns, rows, state.query))
    elif state.mode == "help":
        stream.write(shortcuts(columns, rows))
    stream.flush()


def run(
    stream: TextIO,
    layout: Layout,
    name: str,
    terminal,
    *,
    reprint: Callable[[int, int], Layout] | None = None,
) -> int:
    """Read keys until quit, leaving the tty, title and helper as they were."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return 0

    bridge: Bridge | None = None
    state = State()
    sections = layout.sections
    anchors = list(layout.anchors)
    size = [terminal.columns, terminal.rows]
    # When the last SIGWINCH landed; the reflow waits out REFLOW_DELAY of quiet.
    pending_resize: float | None = None
    # A resize that arrived while a panel covered the document. The reflow is
    # held back until the panel closes and the document is on screen again.
    document_stale = False
    # A jump that must be re-issued to survive Ghostty's post-keystroke
    # scroll-to-bottom: the target row/sentinel, and the times still to fire.
    move_target: int | None = None
    move_at: list[float] = []
    resize_requested = False
    title_saved = False
    wake_read = wake_write = -1
    previous_wakeup: int | None = None
    previous_winch = previous_term = previous_hup = None
    code = 0

    def resized(*_args: object) -> None:
        # Signal handlers may run while stream output is in progress. The wakeup
        # byte lets the normal reader loop redraw at a safe point instead.
        nonlocal resize_requested
        resize_requested = True

    def stop(signum: int, _frame: object) -> None:
        raise Terminated(signum)

    def write_title(current: int | None) -> None:
        stream.write(title(bar(name, sections, current)))
        stream.flush()

    def redraw() -> None:
        draw(stream, state, sections, size[0], size[1])

    def drain_wakeup() -> None:
        while True:
            try:
                if not os.read(wake_read, 1024):
                    return
            except BlockingIOError:
                return

    def relayout() -> None:
        """Erase the stale document from screen and scrollback, then reprint it.

        A resize rewraps every line and moves the centred margin, so the copy in
        the scrollback no longer matches. Erasing the screen (2J) and scrollback
        (3J) first keeps exactly one copy; the reprint reports fresh heading rows.
        sync() holds the caller off until Ghostty has parsed the whole reprint.
        """
        nonlocal anchors
        if reprint is None:
            return
        _debug("relayout | size", size[0], "x", size[1], "| current", state.current)
        stream.write("\x1b[2J\x1b[3J\x1b[H")
        stream.flush()
        anchors = reprint(size[0], size[1]).anchors
        _debug("relayout done | anchors", anchors)
        sync(fd)

    def go(target: int) -> bool:
        """Move Ghostty's viewport to START, END, or a heading (by outline index).

        The document was cleared to the top of the scrollback on the way in, so
        every heading has a known absolute row: one scroll_to_row lands on it,
        with none of the anchor-then-walk that a prompt-mark jump needs.
        """
        if bridge is None:
            return False
        if target == END:
            landed = bridge.scroll_to_bottom()
            _debug("go bottom ->", landed)
            return landed
        if target == START:
            row = 0
        elif 0 <= target < len(sections) and target + 1 < len(anchors):
            row = anchors[target + 1]
        else:
            row = 0
        landed = bridge.scroll_to_row(row)
        _debug("go row", row, "->", landed, "| target", target)
        return landed

    def move(target: int) -> bool:
        """Jump to a mark, then keep re-asserting it briefly (see MOVE_SETTLE)."""
        nonlocal move_target, move_at
        landed = go(target)
        if landed:
            move_target = target
            move_at = [time.monotonic() + delay for delay in MOVE_SETTLE]
        return landed

    def reposition() -> None:
        move(START if state.current is None else state.current)

    try:
        wake_read, wake_write = os.pipe()
        os.set_blocking(wake_read, False)
        os.set_blocking(wake_write, False)
        previous_wakeup = signal.set_wakeup_fd(wake_write)
        previous_winch = signal.signal(signal.SIGWINCH, resized)
        previous_term = signal.signal(signal.SIGTERM, stop)
        previous_hup = signal.signal(signal.SIGHUP, stop)

        # Save the terminal's actual title. OSC 2 has no query mechanism, so a
        # literal document name cannot be a correct restoration.
        stream.write("\x1b[22;2t")
        stream.flush()
        title_saved = True
        bridge = Bridge()
        bridge.discover(stream, settle=lambda: sync(fd))

        with raw_mode(fd):
            # Printing ends at the document's foot; open at its head instead.
            opened_at_start = go(START)
            _debug("opening scroll ->", opened_at_start, "| sections", len(sections),
                   "| anchors", anchors)
            write_title(state.current)
            reader = KeyReader(fd, wake_fd=wake_read)
            while True:
                waits = [REFLOW_DELAY] if pending_resize is not None else []
                if move_at:
                    waits.append(max(0.0, move_at[0] - time.monotonic()))
                key = reader.read(timeout=min(waits) if waits else None)
                if move_at and time.monotonic() >= move_at[0]:
                    move_at.pop(0)
                    if move_target is not None:
                        go(move_target)
                if resize_requested:
                    _debug("SIGWINCH")
                    resize_requested = False
                    drain_wakeup()
                    updated = os.get_terminal_size(fd)
                    size[:] = [updated.columns, updated.lines]
                    pending_resize = time.monotonic()
                if pending_resize is not None and time.monotonic() - pending_resize >= REFLOW_DELAY:
                    pending_resize = None
                    if state.mode == "idle":
                        relayout()
                        reposition()
                    else:
                        # The document is under the alternate screen; reflow it
                        # once the panel closes and it is visible again.
                        document_stale = True
                        redraw()
                if key is None:
                    continue
                opened = state.mode
                old_current = state.current
                state, action = step(state, key, sections)
                _debug(f"key {key!r} -> {action} | current {old_current}->{state.current}")
                if action is Action.QUIT:
                    break
                if action is Action.OPEN:
                    stream.write(ENTER)
                if action is Action.CLOSE or (action is Action.JUMP and opened != "idle"):
                    stream.write(LEAVE)
                    stream.flush()
                    if document_stale:
                        document_stale = False
                        relayout()
                if action is Action.CLOSE:
                    # Leaving the alternate screen can drop the viewport to the
                    # bottom, so put it back on the heading we were reading.
                    reposition()
                if action in {Action.OPEN, Action.REDRAW}:
                    redraw()
                if action is Action.TOP and move(START):
                    state = replace(state, current=None)
                    write_title(None)
                if action is Action.BOTTOM and move(END):
                    state = replace(state, current=None)
                    write_title(None)
                if action is Action.JUMP and state.current is not None:
                    # A refused jump leaves the viewport alone, so the tracked
                    # position must go back to where the document really is.
                    if move(state.current):
                        write_title(state.current)
                    else:
                        state = replace(state, current=old_current)
    except Terminated as terminated:
        code = 128 + terminated.args[0]
    except KeyboardInterrupt:
        # Ctrl-C is a byte in raw mode; an external interrupt still leaves a
        # reader cleanly, as documented for interactive mode.
        code = 0
    finally:
        try:
            if state.mode != "idle":
                stream.write(LEAVE)
            if title_saved:
                stream.write("\x1b[23;2t")
            stream.flush()
        finally:
            if previous_winch is not None:
                signal.signal(signal.SIGWINCH, previous_winch)
            if previous_term is not None:
                signal.signal(signal.SIGTERM, previous_term)
            if previous_hup is not None:
                signal.signal(signal.SIGHUP, previous_hup)
            if previous_wakeup is not None:
                signal.set_wakeup_fd(previous_wakeup)
            if bridge is not None:
                bridge.close()
            if wake_read >= 0:
                os.close(wake_read)
            if wake_write >= 0:
                os.close(wake_write)
            os.close(fd)
    return code
