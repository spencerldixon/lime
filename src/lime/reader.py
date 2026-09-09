"""Stay resident for keys while Ghostty keeps scrolling, selection and search."""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TextIO

from lime.ghostty import RUNNER_UNAVAILABLE, START, Bridge, title
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


class Terminated(Exception):
    """Raised from a SIGTERM/SIGHUP handler to unwind the loop and clean up."""


@dataclass(frozen=True)
class State:
    mode: str = "idle"
    query: str = ""
    selected: int = 0
    current: int | None = None


def bar(name: str, sections: list[Section], current: int | None) -> str:
    """The window title: document name, plus the last section jumped to, if any."""
    if current is None or not sections:
        return f"lime · {name} · ? for keys"
    section = sections[current]
    return f"lime · {name} · {section.title} {current + 1}/{len(sections)} · ? for keys"


def step(state: State, key: Key | str, sections: list[Section]) -> tuple[State, Action | None]:
    """The whole interaction as one pure decision: next state, plus what to do."""
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


def picker_step(
    state: State, key: Key | str, sections: list[Section]
) -> tuple[State, Action | None]:
    """Key handling while the table of contents is open: filter, move, choose."""
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


def draw(
    stream: TextIO,
    state: State,
    sections: list[Section],
    columns: int,
    rows: int,
    jump: str | None,
) -> None:
    """Render whichever overlay is open into its already-reserved rows."""
    if state.mode == "toc":
        entries = filtered(sections, state.query)
        stream.write(picker(entries, state.selected, state.query, columns, rows))
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
    if mode != RUNNER_UNAVAILABLE:
        bridge.discover(stream)
    jump = None if bridge.available else RUNNER_UNAVAILABLE
    state = State()
    size = [terminal.columns, terminal.rows]

    def resized(*_args: object) -> None:
        updated = os.get_terminal_size(fd)
        size[:] = [updated.columns, updated.lines]
        if state.mode != "idle":
            draw(stream, state, sections, size[0], size[1], jump)

    def stop(signum: int, _frame: object) -> None:
        raise Terminated(signum)

    previous_winch = signal.signal(signal.SIGWINCH, resized)
    previous_term = signal.signal(signal.SIGTERM, stop)
    previous_hup = signal.signal(signal.SIGHUP, stop)
    code = 0
    try:
        with raw_mode(fd):
            # Printing ends at the document's foot; open at its head instead.
            # Every document has a start mark, so this works without headings.
            if not bridge.jump(START, len(sections)):
                jump = RUNNER_UNAVAILABLE
            stream.write(title(bar(name, sections, state.current)))
            stream.flush()
            while True:
                key = read_key(fd)
                if key is None:
                    continue
                opened = state.mode
                state, action = step(state, key, sections)
                if action is Action.QUIT:
                    break
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
                        jump = RUNNER_UNAVAILABLE
                    stream.write(title(bar(name, sections, state.current)))
                    stream.flush()
    except Terminated as terminated:
        code = 128 + terminated.args[0]
    except KeyboardInterrupt:
        code = 130
    finally:
        signal.signal(signal.SIGWINCH, previous_winch)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGHUP, previous_hup)
        if state.mode != "idle":
            stream.write(erase())
        stream.write(title(name))
        stream.flush()
        bridge.close()
        os.close(fd)
    return code
