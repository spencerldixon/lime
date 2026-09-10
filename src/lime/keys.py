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


class KeyReader:
    """Decode one key at a time without dropping bytes from bursty terminals."""

    def __init__(
        self, fd: int, *, timeout: float = 0.05, wake_fd: int | None = None, chunk_size: int = 64
    ) -> None:
        self.fd = fd
        self.timeout = timeout
        self.wake_fd = wake_fd
        self.chunk_size = chunk_size
        self.buffer = b""

    def read(self, timeout: float | None = None) -> Key | str | None:
        """Wait for a key, retaining unread bytes for the next call.

        A bare Escape is ambiguous with the beginning of a terminal sequence, so
        only that prefix gets a short bounded wait. Every other idle wait blocks
        unless timeout is provided.
        """
        while True:
            event, remainder = decode(self.buffer)
            ambiguous_escape = event is Key.ESCAPE and self.buffer == b"\x1b"
            if ambiguous_escape:
                ready, _, _ = select.select(
                    [self.fd, *([self.wake_fd] if self.wake_fd is not None else [])],
                    [],
                    [],
                    self.timeout,
                )
                if self.wake_fd is not None and self.wake_fd in ready:
                    return None
                if not ready:
                    # Escape is the only partial input with a useful standalone
                    # meaning. Preserve bytes that followed it for the next event.
                    self.buffer = remainder if event is Key.ESCAPE else self.buffer[1:]
                    return Key.ESCAPE
                chunk = os.read(self.fd, self.chunk_size)
                if not chunk:
                    return Key.EOF
                self.buffer += chunk
                continue
            if event is not None:
                self.buffer = remainder
                return event
            if remainder != self.buffer:
                self.buffer = remainder
                continue
            ambiguous_escape = self.buffer.startswith(b"\x1b")
            if ambiguous_escape:
                ready, _, _ = select.select(
                    [self.fd, *([self.wake_fd] if self.wake_fd is not None else [])],
                    [],
                    [],
                    self.timeout,
                )
                if self.wake_fd is not None and self.wake_fd in ready:
                    return None
                if not ready:
                    self.buffer = self.buffer[1:]
                    return Key.ESCAPE
                chunk = os.read(self.fd, self.chunk_size)
                if not chunk:
                    return Key.EOF
                self.buffer += chunk
                continue
            ready, _, _ = select.select(
                [self.fd, *([self.wake_fd] if self.wake_fd is not None else [])],
                [],
                [],
                timeout,
            )
            if self.wake_fd is not None and self.wake_fd in ready:
                return None
            if not ready:
                if timeout is not None:
                    return None
                continue
            chunk = os.read(self.fd, self.chunk_size)
            if not chunk:
                return Key.EOF
            self.buffer += chunk


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
    settings[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
    settings[6][termios.VMIN] = 0
    settings[6][termios.VTIME] = 0
    try:
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)


def read_key(fd: int, timeout: float = 0.05) -> Key | str | None:
    """Read one event compatibly, leaving any burst remainder in the tty queue.

    Resident callers should keep a :class:`KeyReader` for better throughput.
    """
    # A one-byte reader makes this stateless wrapper safe for callers that
    # invoke it repeatedly; the session reader uses a larger read and a buffer.
    return KeyReader(fd, timeout=timeout, chunk_size=1).read()
