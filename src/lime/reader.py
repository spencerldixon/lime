"""Stay resident for keys while Ghostty keeps scrolling, selection and search."""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TextIO

from lime.ghostty import END, START, Bridge, _debug, title
from lime.keys import Key, KeyReader, raw_mode
from lime.outline import Section
from lime.overlay import ENTER, LEAVE, contents, shortcuts
from lime.render import END_MARK
from lime.terminal import sync


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
class State:
    mode: str = "idle"
    selected: int = 0
    current: int | None = None


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
        return replace(state, mode="toc", selected=start), Action.OPEN
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
    """Key handling while the contents are open: move the row, choose, or close."""
    if key in {Key.ESCAPE, Key.INTERRUPT, "q", "t"}:
        return replace(state, mode="idle"), Action.CLOSE
    if key is Key.ENTER:
        if not sections:
            return replace(state, mode="idle"), Action.CLOSE
        return replace(state, mode="idle", current=state.selected), Action.JUMP
    if not sections:
        return state, None
    if key in {Key.DOWN, Key.NEXT, "j"}:
        return replace(state, selected=min(state.selected + 1, len(sections) - 1)), Action.REDRAW
    if key in {Key.UP, Key.PREVIOUS, "k"}:
        return replace(state, selected=max(state.selected - 1, 0)), Action.REDRAW
    if key == "g":
        return replace(state, selected=0), Action.REDRAW
    if key == "G":
        return replace(state, selected=len(sections) - 1), Action.REDRAW
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
        stream.write(contents(sections, state.selected, columns, rows))
    elif state.mode == "help":
        stream.write(shortcuts(columns, rows))
    stream.flush()


def run(stream: TextIO, sections: list[Section], name: str, terminal) -> int:
    """Read keys until quit, leaving the tty, title and helper as they were."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return 0

    bridge: Bridge | None = None
    state = State()
    size = [terminal.columns, terminal.rows]
    # Rows the document owes the end anchor because the window grew while a
    # panel covered it; written once the alternate screen is gone.
    deferred_rows = 0
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
        if bridge.available:
            # The bridge counts prompt marks from the viewport. Keep every
            # document mark above a fresh full viewport, and create the final
            # END mark only in a session that can use native movement.
            stream.write(END_MARK + "\n" * (size[1] + 1))
            stream.flush()

        with raw_mode(fd):
            # Printing ends at the document's foot; open at its head instead.
            opened_at_start = bridge.jump(START, len(sections))
            _debug("opening jump ->", opened_at_start, "| sections", len(sections),
                   "| rows", size[1])
            write_title(state.current)
            reader = KeyReader(fd, wake_fd=wake_read)
            while True:
                key = reader.read()
                if resize_requested:
                    _debug("SIGWINCH")
                    resize_requested = False
                    drain_wakeup()
                    old_rows = size[1]
                    updated = os.get_terminal_size(fd)
                    size[:] = [updated.columns, updated.lines]
                    # The end anchor needs a full viewport of unmarked space
                    # below it. Growing the window otherwise pulls old marks
                    # into the active page and changes Ghostty's mark count.
                    growth = max(0, size[1] - old_rows) if bridge.available else 0
                    if state.mode == "idle":
                        if growth:
                            stream.write("\n" * growth)
                            stream.flush()
                    else:
                        # Those rows would land on the alternate screen while a
                        # panel is open, so the document collects them on close.
                        deferred_rows += growth
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
                    stream.write(LEAVE + "\n" * deferred_rows)
                    stream.flush()
                    deferred_rows = 0
                if action is Action.CLOSE:
                    # Leaving the alternate screen can drop the viewport to the
                    # bottom, so put it back on the heading we were reading.
                    bridge.jump(START if state.current is None else state.current, len(sections))
                if action in {Action.OPEN, Action.REDRAW}:
                    redraw()
                if action is Action.TOP and bridge.jump(START, len(sections)):
                    state = replace(state, current=None)
                    write_title(None)
                if action is Action.BOTTOM and bridge.jump(END, len(sections)):
                    state = replace(state, current=None)
                    write_title(None)
                if action is Action.JUMP and state.current is not None:
                    # A refused jump leaves the viewport alone, so the tracked
                    # position must go back to where the document really is.
                    if bridge.jump(state.current, len(sections)):
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
