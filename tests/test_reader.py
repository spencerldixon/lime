import fcntl
import io
import os
import signal
import struct
import termios
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_terminal import assert_restored

from lime import reader
from lime.ghostty import Bridge
from lime.keys import Key
from lime.outline import Section
from lime.reader import Action, State, bar, run, step
from lime.terminal import Terminal

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


def test_q_is_filter_text_inside_the_picker():
    # q must be filter text inside the picker, not a quit key.
    state = State("toc", "", 0, None)
    assert step(state, "q", SECTIONS)[0].query == "q"


def test_bar_omits_the_section_before_any_jump():
    assert bar("README.md", SECTIONS, None) == "lime · README.md · ? for keys"


def test_bar_names_the_last_jumped_section():
    assert bar("README.md", SECTIONS, 1) == "lime · README.md · Configure 2/3 · ? for keys"


def test_the_bar_claims_no_section_at_the_document_start():
    # The reader opens at the start mark, which is not any section.
    assert bar("README.md", SECTIONS, None) == "lime · README.md · ? for keys"


# --- run(): terminal residency, signal handling and overlay bookkeeping ---
#
# run() opens "/dev/tty" itself and always constructs a real Bridge(), so these
# tests redirect both: /dev/tty onto a pty we control, and Bridge onto a scripted
# fake that never shells out to osascript.


class FakeRunner:
    """A scripted AppleScript stand-in: never shells out, records every call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.scripts = []

    def __call__(self, script):
        self.scripts.append(script)
        return self.replies.pop(0) if self.replies else "false"

    def close(self):
        pass


def open_pty():
    """A pty pair with a real window size, so TIOCGWINSZ has something to report."""
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    return master, slave


def patch_tty(monkeypatch, slave):
    """Redirect the reader's /dev/tty open onto a duplicate of our pty slave."""
    real_open = os.open

    def fake_open(path, flags, *rest):
        return os.dup(slave) if path == "/dev/tty" else real_open(path, flags, *rest)

    monkeypatch.setattr(reader.os, "open", fake_open)


def wait_until(predicate, timeout=2.0):
    """Poll a condition instead of guessing a sleep; never blocks past timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
    return False


def press(master, stream, data, ready, timeout=2.0):
    """Write one keystroke and wait for its distinguishing effect before returning.

    read_key() decodes exactly one event out of whatever a single os.read() call
    returns, and drops anything left over rather than carrying it to the next
    call. So a multi-key sequence must never be written faster than run() can
    consume it, or a keystroke silently vanishes; each key here waits for proof
    that run() has already processed the previous one.
    """
    os.write(master, data)
    return wait_until(ready, timeout)


def run_with_timeout(seconds, *args):
    """A hard backstop: a real bug in run() fails the test instead of hanging it."""

    def alarm(signum, frame):
        raise TimeoutError("run() did not return before the test's safety timeout")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(seconds)
    try:
        return run(*args)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_run_opens_at_the_document_start_before_reading_any_key(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        os.write(master, b"q")
        result = run_with_timeout(5, stream, SECTIONS, "README.md", Terminal(80, 24), "auto")
        assert result == 0
        # discover() then the opening jump (scroll_to_bottom, jump_to_prompt) happen
        # before the loop ever reads a key: 3 headings -> 4 marks, start is mark 0.
        assert "jump_to_prompt:-4" in runner.scripts[2]
        # The opening jump lands nowhere sections-shaped, so the bar stays bare.
        assert "lime · README.md · ? for keys" in stream.getvalue()
    finally:
        os.close(master)
        os.close(slave)


def test_run_opens_at_the_start_mark_with_no_headings(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        os.write(master, b"q")
        result = run_with_timeout(5, stream, [], "README.md", Terminal(80, 24), "auto")
        assert result == 0
        assert "jump_to_prompt:-1" in runner.scripts[2]
    finally:
        os.close(master)
        os.close(slave)


def test_run_falls_back_to_reprint_when_the_opening_jump_fails(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        # Discovery succeeds, but the jump itself fails: the bridge goes dead.
        runner = FakeRunner(["SURFACE-1", "false"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            # open help, close help, then quit
            if not press(master, stream, b"?", lambda: "Keys" in stream.getvalue()):
                return
            if not press(master, stream, b"q", lambda: "\r\x1b[J" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(
                5, stream, SECTIONS, "README.md", Terminal(80, 24), "auto"
            )
            peer.result(timeout=5)
        assert result == 0
        # The help panel must say so in plain, user-visible text.
        assert "reprint" in stream.getvalue()
    finally:
        os.close(master)
        os.close(slave)


def test_run_skips_discovery_when_configured_to_always_reprint(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1"])  # would satisfy discover, but must go unused
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        os.write(master, b"q")
        result = run_with_timeout(5, stream, SECTIONS, "README.md", Terminal(80, 24), "reprint")
        assert result == 0
        assert runner.scripts == []
    finally:
        os.close(master)
        os.close(slave)


def test_run_restores_terminal_and_title_on_quit(monkeypatch):
    master, slave = open_pty()
    original = termios.tcgetattr(slave)
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        os.write(master, b"q")
        result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
        assert result == 0
        assert_restored(slave, original)
        assert stream.getvalue().endswith("\x1b]2;doc.md\x1b\\")
    finally:
        os.close(master)
        os.close(slave)


def test_run_pairs_overlay_open_and_close(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            # open the picker, Escape closes it, then quit
            if not press(master, stream, b"t", lambda: "\x1b[?2026h" in stream.getvalue()):
                return
            if not press(master, stream, b"\x1b", lambda: "\r\x1b[J" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
            peer.result(timeout=5)
        assert result == 0
        output = stream.getvalue()
        assert "\x1b[?2026h" in output  # the picker frame was drawn
        assert output.count("\r\x1b[J") == 1  # erase() fired exactly once, on close
    finally:
        os.close(master)
        os.close(slave)


def test_run_erases_a_jump_that_closes_the_picker(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def frames(n):
            return lambda: stream.getvalue().count("\x1b[?2026h") >= n

        def send():
            # filter to "ins", jump to the first match, then quit
            for count, key in enumerate((b"t", b"i", b"n", b"s"), start=1):
                if not press(master, stream, key, frames(count)):
                    return
            if not press(master, stream, b"\r", lambda: "Install 1/3" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
            peer.result(timeout=5)
        assert result == 0
        output = stream.getvalue()
        assert output.count("\r\x1b[J") == 1
        assert "lime · doc.md · Install 1/3 · ? for keys" in output
    finally:
        os.close(master)
        os.close(slave)


def test_run_reports_a_window_too_short_for_the_overlay(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            if not press(master, stream, b"t", lambda: "too short" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 8), "auto")
            peer.result(timeout=5)
        assert result == 0
        assert "too short" in stream.getvalue()
    finally:
        os.close(master)
        os.close(slave)


def test_run_restores_the_previous_sigwinch_handler(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        before = signal.getsignal(signal.SIGWINCH)
        os.write(master, b"q")
        run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
        assert signal.getsignal(signal.SIGWINCH) is before
    finally:
        os.close(master)
        os.close(slave)


def test_resize_redraws_only_while_an_overlay_is_open(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        observed = {}

        def send():
            # Guarded waits: if any step never completes, this thread bails out
            # without acting, and the test's own alarm turns the hang into a
            # clean failure instead of blocking the suite forever.
            #
            # Wait for the *last* write run() makes before it blocks on the
            # first read_key() call (the opening title, after discover() and
            # the opening jump), not just any output: discover() alone writes
            # the picker-unrelated nonce/lime titles well before the signal
            # handlers are even installed, which would race a signal sent any
            # earlier and blame normal startup output on resized().
            if not wait_until(lambda: "? for keys" in stream.getvalue()):
                return
            baseline = len(stream.getvalue())
            os.kill(os.getpid(), signal.SIGWINCH)  # idle: resized() must no-op
            time.sleep(0.05)
            observed["idle_growth"] = len(stream.getvalue()) - baseline
            os.write(master, b"t")
            if not wait_until(lambda: "\x1b[?2026h" in stream.getvalue()):
                return
            marker = len(stream.getvalue())
            os.kill(os.getpid(), signal.SIGWINCH)  # toc open: must redraw
            observed["redrew"] = wait_until(lambda: len(stream.getvalue()) > marker)
            if not press(master, stream, b"\x1b", lambda: "\r\x1b[J" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
            peer.result(timeout=5)
        assert result == 0
        assert observed["idle_growth"] == 0
        assert observed["redrew"] is True
        assert stream.getvalue().count("\x1b[?2026h") == 2
    finally:
        os.close(master)
        os.close(slave)


def test_sigterm_cleans_up_the_terminal_and_any_open_overlay(monkeypatch):
    master, slave = open_pty()
    original = termios.tcgetattr(slave)
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            os.write(master, b"?")  # open help, then leave it open
            if wait_until(lambda: "Keys" in stream.getvalue()):
                os.kill(os.getpid(), signal.SIGTERM)

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
            peer.result(timeout=5)
        assert result == 128 + signal.SIGTERM
        assert_restored(slave, original)
        output = stream.getvalue()
        assert output.count("\r\x1b[J") == 1  # the still-open help panel was erased
        assert output.endswith("\x1b]2;doc.md\x1b\\")
    finally:
        os.close(master)
        os.close(slave)


def test_sighup_also_cleans_up(monkeypatch):
    master, slave = open_pty()
    original = termios.tcgetattr(slave)
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            if wait_until(lambda: bool(stream.getvalue())):
                os.kill(os.getpid(), signal.SIGHUP)

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
            peer.result(timeout=5)
        assert result == 128 + signal.SIGHUP
        assert_restored(slave, original)
    finally:
        os.close(master)
        os.close(slave)


def test_sigterm_and_sighup_handlers_are_restored_afterwards(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        before_term = signal.getsignal(signal.SIGTERM)
        before_hup = signal.getsignal(signal.SIGHUP)
        os.write(master, b"q")
        run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
        assert signal.getsignal(signal.SIGTERM) is before_term
        assert signal.getsignal(signal.SIGHUP) is before_hup
    finally:
        os.close(master)
        os.close(slave)


def test_an_unexpected_exception_still_restores_the_terminal(monkeypatch):
    master, slave = open_pty()
    original = termios.tcgetattr(slave)
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))

        def boom(state, key, sections):
            raise ValueError("boom")

        monkeypatch.setattr(reader, "step", boom)
        stream = io.StringIO()
        os.write(master, b"q")
        with pytest.raises(ValueError, match="boom"):
            run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), "auto")
        assert_restored(slave, original)
        assert stream.getvalue().endswith("\x1b]2;doc.md\x1b\\")
    finally:
        os.close(master)
        os.close(slave)


def test_missing_controlling_terminal_is_a_quiet_noop(monkeypatch):
    def fake_open(path, flags, *rest):
        if path == "/dev/tty":
            raise OSError("no tty")
        return os.open(path, flags, *rest)

    monkeypatch.setattr(reader.os, "open", fake_open)
    assert run(io.StringIO(), SECTIONS, "doc.md", Terminal(80, 24), "auto") == 0
