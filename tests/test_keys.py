import os
import termios

import pytest
from test_terminal import assert_restored

from lime.keys import Key, decode, raw_mode


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
