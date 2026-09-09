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
