import os
import select
import termios
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from lime import terminal
from lime.terminal import TerminalTheme, read_palette


def assert_restored(fd, original):
    restored = termios.tcgetattr(fd)
    # Darwin sets this kernel-maintained flag on returning to canonical mode.
    restored[3] &= ~getattr(termios, "PENDIN", 0)
    expected = original.copy()
    expected[3] &= ~getattr(termios, "PENDIN", 0)
    assert restored == expected


@pytest.mark.parametrize("ending", [b"\x07", b"\x1b\\"])
def test_palette_query_uses_real_theme_and_restores_terminal(ending):
    master, slave = os.openpty()
    original = termios.tcgetattr(slave)

    def ghostty():
        assert select.select([master], [], [], 1)[0]
        assert os.read(master, 1024) == b"\x1b]4;2;?;6;?;4;?\x1b\\\x1b]10;?\x1b\\\x1b]11;?\x1b\\"
        for index, color in [(2, b"1234/5678/9abc"), (6, b"aa/bb/cc"), (4, b"1/2/3")]:
            os.write(master, b"\x1b]4;" + str(index).encode() + b";rgb:" + color + ending)
        os.write(master, b"\x1b]10;rgb:ff/ff/ff" + ending + b"\x1b]11;rgb:00/00/00" + ending)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(ghostty)
            assert read_palette(slave) == TerminalTheme(
                ((18, 86, 154), (170, 187, 204), (17, 34, 51)), (255, 255, 255), (0, 0, 0)
            )
            peer.result()
        assert_restored(slave, original)
    finally:
        os.close(master)
        os.close(slave)


def test_query_timeout_restores_terminal():
    master, slave = os.openpty()
    original = termios.tcgetattr(slave)
    try:
        assert read_palette(slave, timeout=0.01) is None
        assert_restored(slave, original)
    finally:
        os.close(master)
        os.close(slave)


def test_queued_input_is_left_alone():
    master, slave = os.openpty()
    try:
        os.write(master, b"next command\n")
        assert select.select([slave], [], [], 1)[0]
        assert read_palette(slave) is None
        assert os.read(slave, 1024) == b"next command\n"
    finally:
        os.close(master)
        os.close(slave)


def test_sync_gives_up_quietly_when_nothing_answers():
    master, slave = os.openpty()
    try:
        original = termios.tcgetattr(slave)
        assert terminal.sync(slave, timeout=0.05) is False
        assert_restored(slave, original)  # input flags put back
    finally:
        os.close(master)
        os.close(slave)


def test_sync_returns_true_once_the_terminal_answers():
    master, slave = os.openpty()
    try:
        os.write(master, b"\x1b[0n")
        time.sleep(0.05)
        assert terminal.sync(slave, timeout=0.5) is True
    finally:
        os.close(master)
        os.close(slave)


def test_sync_asks_with_a_device_status_report():
    master, slave = os.openpty()
    try:
        terminal.sync(slave, timeout=0.05)
        assert b"\x1b[5n" in os.read(master, 64)
    finally:
        os.close(master)
        os.close(slave)
