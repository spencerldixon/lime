import os
import termios
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_terminal import assert_restored

from lime.keys import Key, KeyReader, decode, raw_mode, read_key


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


def test_lone_escape_decodes_as_escape_for_the_public_decoder():
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


def test_read_key_reads_a_plain_character():
    master, slave = os.openpty()
    try:
        with raw_mode(slave):
            os.write(master, b"t")
            assert read_key(slave) == "t"
    finally:
        os.close(master)
        os.close(slave)


def test_read_key_reads_an_escape_sequence_delivered_in_one_write():
    master, slave = os.openpty()
    try:
        with raw_mode(slave):
            os.write(master, b"\x1b[A")
            assert read_key(slave) == Key.UP
    finally:
        os.close(master)
        os.close(slave)


def test_read_key_reassembles_a_sequence_split_across_two_writes():
    # A real delay between the two writes forces read_key's first os.read() to
    # see only the prefix, exercising the partial-sequence continuation path.
    master, slave = os.openpty()
    try:
        with raw_mode(slave):

            def send():
                os.write(master, b"\x1b[")
                time.sleep(0.05)
                os.write(master, b"A")

            with ThreadPoolExecutor(max_workers=1) as pool:
                peer = pool.submit(send)
                assert read_key(slave, timeout=0.5) == Key.UP
                peer.result(timeout=2)
    finally:
        os.close(master)
        os.close(slave)


def test_session_reader_preserves_every_key_from_a_single_pty_burst():
    master, slave = os.openpty()
    try:
        with raw_mode(slave):
            os.write(master, b"t?\x1b[Aq")
            reader = KeyReader(slave)
            assert [reader.read() for _ in range(4)] == ["t", "?", Key.UP, "q"]
    finally:
        os.close(master)
        os.close(slave)


def test_session_reader_discards_unknown_escape_sequences_without_losing_following_keys():
    master, slave = os.openpty()
    try:
        with raw_mode(slave):
            os.write(master, b"\x1b[3~t")
            assert KeyReader(slave).read() == "t"
    finally:
        os.close(master)
        os.close(slave)


def test_lone_escape_returns_only_after_the_bounded_escape_wait():
    master, slave = os.openpty()
    try:
        with raw_mode(slave):
            os.write(master, b"\x1b")
            started = time.monotonic()
            assert KeyReader(slave, timeout=0.02).read() is Key.ESCAPE
            assert time.monotonic() - started >= 0.015
    finally:
        os.close(master)
        os.close(slave)


def test_raw_mode_delivers_ctrl_c_as_a_cancel_key():
    master, slave = os.openpty()
    try:
        with raw_mode(slave):
            os.write(master, b"\x03")
            assert KeyReader(slave).read() is Key.INTERRUPT
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("prefix", [b"", b"\x1b", b"\x1b[", b"\xc3"])
def test_reader_returns_eof_once_the_input_fd_is_closed(prefix):
    read_end, write_end = os.pipe()
    try:
        os.close(write_end)
        reader = KeyReader(read_end)
        reader.buffer = prefix
        assert reader.read() is Key.EOF
    finally:
        os.close(read_end)


@pytest.mark.parametrize(
    ("prefix", "suffix", "expected"),
    [
        (b"", b"t", "t"),
        (b"\x1b", b"[A", Key.UP),
        (b"\x1b[", b"A", Key.UP),
        (b"\xc3", b"\xa9", "é"),
    ],
)
def test_wakeup_preserves_partial_input_and_queued_keys(prefix, suffix, expected):
    read_end, write_end = os.pipe()
    wake_read, wake_write = os.pipe()
    try:
        reader = KeyReader(read_end, wake_fd=wake_read)
        reader.buffer = prefix
        os.write(write_end, suffix + b"q")
        os.write(wake_write, b"\0")
        assert reader.read(timeout=0) is None  # Resize wins when both descriptors are ready.
        assert reader.buffer == prefix
        os.read(wake_read, 1)
        assert reader.read(timeout=0) == expected
        assert reader.read(timeout=0) == "q"
    finally:
        for fd in (read_end, write_end, wake_read, wake_write):
            os.close(fd)


@pytest.mark.parametrize(
    ("prefix", "suffix", "expected"), [(b"", b"t", "t"), (b"\xc3", b"\xa9", "é")]
)
def test_idle_timeout_keeps_partial_input_for_the_next_read(prefix, suffix, expected):
    read_end, write_end = os.pipe()
    try:
        reader = KeyReader(read_end)
        reader.buffer = prefix
        assert reader.read(timeout=0) is None
        assert reader.buffer == prefix
        os.write(write_end, suffix)
        assert reader.read(timeout=0) == expected
    finally:
        os.close(read_end)
        os.close(write_end)


@pytest.mark.parametrize("prefix", [b"\x1b[", b"\x1bO", b"\x1b[1;"])
def test_escape_timeout_leaves_the_rest_of_a_truncated_sequence(prefix):
    read_end, write_end = os.pipe()
    try:
        reader = KeyReader(read_end, timeout=0)
        os.write(write_end, prefix)
        assert reader.read(timeout=0) is Key.ESCAPE
        assert "".join(reader.read(timeout=0) for _ in prefix[1:]) == prefix[1:].decode()
    finally:
        os.close(read_end)
        os.close(write_end)
