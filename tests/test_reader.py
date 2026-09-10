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

from lime import ghostty, reader
from lime.ghostty import ATTEMPTS, Bridge
from lime.keys import Key
from lime.outline import Section
from lime.overlay import ENTER, LEAVE
from lime.reader import Action, State, bar, run, step
from lime.render import END_MARK
from lime.terminal import Terminal

SECTIONS = [
    Section("Install", 2, 0, 0),
    Section("Configure", 2, 1, 4),
    Section("Inspect", 2, 2, 8),
]
IDLE = State("idle", 0, None)


def test_t_opens_the_table_of_contents():
    state, action = step(IDLE, "t", SECTIONS)
    assert state.mode == "toc" and action is Action.OPEN


def test_q_quits_from_idle():
    assert step(IDLE, "q", SECTIONS)[1] is Action.QUIT


def test_interrupt_quits():
    assert step(IDLE, Key.INTERRUPT, SECTIONS)[1] is Action.QUIT


@pytest.mark.parametrize("state", [State("toc"), State("idle")])
def test_ctrl_d_quits_from_every_overlay(state):
    assert step(state, Key.EOF, SECTIONS)[1] is Action.QUIT


def test_g_and_shift_g_scroll():
    assert step(IDLE, "g", SECTIONS)[1] is Action.TOP
    assert step(IDLE, "G", SECTIONS)[1] is Action.BOTTOM


def test_n_and_p_step_between_adjacent_headings():
    state, action = step(IDLE, "n", SECTIONS)
    assert action is Action.JUMP and state.current == 0
    state, action = step(state, "n", SECTIONS)
    assert action is Action.JUMP and state.current == 1
    state, action = step(state, "p", SECTIONS)
    assert action is Action.JUMP and state.current == 0


def test_p_with_no_tracked_position_does_nothing():
    assert step(IDLE, "p", SECTIONS) == (IDLE, None)


def test_n_stops_at_the_last_section():
    state = State("idle", 0, 2)
    assert step(state, "n", SECTIONS) == (state, None)


def test_p_stops_at_the_first_section():
    state = State("idle", 0, 0)
    assert step(state, "p", SECTIONS) == (state, None)


def test_t_opens_the_contents_on_the_current_heading():
    state, _ = step(State("idle", 0, 1), "t", SECTIONS)
    assert state.mode == "toc" and state.selected == 1


def test_t_opens_at_the_top_before_any_jump():
    assert step(IDLE, "t", SECTIONS)[0].selected == 0


def test_arrows_move_the_selection():
    state = State("toc", 0, None)
    assert step(state, Key.DOWN, SECTIONS)[0].selected == 1
    assert step(state, Key.UP, SECTIONS)[0].selected == 0


def test_j_and_k_move_the_selection():
    state = State("toc", 0, None)
    assert step(state, "j", SECTIONS)[0].selected == 1
    assert step(State("toc", 2, None), "k", SECTIONS)[0].selected == 1


def test_ctrl_n_and_ctrl_p_also_move_the_selection():
    state = State("toc", 0, None)
    assert step(state, Key.NEXT, SECTIONS)[0].selected == 1
    assert step(State("toc", 2, None), Key.PREVIOUS, SECTIONS)[0].selected == 1


def test_the_selection_stops_at_both_ends_rather_than_wrapping():
    assert step(State("toc", 0, None), Key.UP, SECTIONS)[0].selected == 0
    assert step(State("toc", 2, None), Key.DOWN, SECTIONS)[0].selected == 2


def test_g_and_shift_g_reach_the_first_and_last_heading():
    assert step(State("toc", 1, None), "g", SECTIONS)[0].selected == 0
    assert step(State("toc", 1, None), "G", SECTIONS)[0].selected == 2


def test_enter_jumps_to_the_selected_heading_and_closes():
    state, action = step(State("toc", 2, None), Key.ENTER, SECTIONS)
    assert action is Action.JUMP and state.current == 2 and state.mode == "idle"


def test_enter_in_a_document_without_headings_just_closes():
    state, action = step(State("toc", 0, None), Key.ENTER, [])
    assert action is Action.CLOSE and state.mode == "idle"


def test_escape_closes_without_moving():
    state, action = step(State("toc", 1, 0), Key.ESCAPE, SECTIONS)
    assert state.mode == "idle" and state.current == 0 and action is Action.CLOSE


def test_q_and_t_also_close_the_contents():
    for key in ("q", "t"):
        state, action = step(State("toc", 1, 0), key, SECTIONS)
        assert action is Action.CLOSE and state.mode == "idle" and state.current == 0


def test_bar_omits_the_section_before_any_jump():
    assert bar("README.md", SECTIONS, None) == "lime · README.md"


def test_bar_names_the_last_jumped_section():
    assert bar("README.md", SECTIONS, 1) == "lime · README.md · Configure 2/3"


def test_the_bar_claims_no_section_at_the_document_start():
    # The reader opens at the start mark, which is not any section.
    assert bar("README.md", SECTIONS, None) == "lime · README.md"


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
        self.closed = False

    def __call__(self, script):
        self.scripts.append(script)
        return self.replies.pop(0) if self.replies else "false"

    def close(self):
        self.closed = True


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
    # Nothing is emulating a terminal on the far end of this pty, so neither the
    # parser barrier nor the discovery retry should spend real time waiting.
    monkeypatch.setattr(reader, "sync", lambda _fd: True)
    monkeypatch.setattr(ghostty.time, "sleep", lambda _seconds: None)


def wait_until(predicate, timeout=2.0):
    """Poll a condition instead of guessing a sleep; never blocks past timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
    return False


def press(master, stream, data, ready, timeout=2.0):
    """Write one keystroke and wait for its distinguishing effect before returning.

    Each key waits for proof that the reader has processed the previous one,
    keeping these interaction tests deterministic even though the real session
    reader now retains all bytes from a burst.
    """
    os.write(master, data)
    return wait_until(ready, timeout)


def run_with_timeout(seconds, *args, **kwargs):
    """A hard backstop: a real bug in run() fails the test instead of hanging it."""

    def alarm(signum, frame):
        raise TimeoutError("run() did not return before the test's safety timeout")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(seconds)
    try:
        return run(*args, **kwargs)
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
        result = run_with_timeout(5, stream, SECTIONS, "README.md", Terminal(80, 24))
        assert result == 0
        # Discovery then the batched opening jump happen before the first key.
        # The end mark plus blank viewport leaves 5 marks above the anchor.
        assert "jump_to_prompt:-5" in runner.scripts[1]
        assert stream.getvalue().index(END_MARK) < stream.getvalue().index("lime · README.md")
        # The opening jump lands nowhere sections-shaped, so the bar stays bare.
        assert "lime · README.md" in stream.getvalue()
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
        result = run_with_timeout(5, stream, [], "README.md", Terminal(80, 24))
        assert result == 0
        assert "jump_to_prompt:-2" in runner.scripts[1]
    finally:
        os.close(master)
        os.close(slave)


def test_discovery_failure_does_not_add_blank_native_anchor_rows(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner([""] * (ATTEMPTS + 1))
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        os.write(master, b"q")
        assert run_with_timeout(5, stream, SECTIONS, "README.md", Terminal(80, 24)) == 0
        assert END_MARK not in stream.getvalue()
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
        result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
        assert result == 0
        assert_restored(slave, original)
        assert stream.getvalue().endswith("\x1b[23;2t")
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
            # open the contents, Escape closes it, then quit
            if not press(master, stream, b"t", lambda: "\x1b[?2026h" in stream.getvalue()):
                return
            if not press(master, stream, b"\x1b", lambda: LEAVE in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        output = stream.getvalue()
        assert output.count(ENTER) == 1  # the alternate screen was entered once
        assert output.count(LEAVE) == 1  # and left exactly once, on close
    finally:
        os.close(master)
        os.close(slave)


def test_run_leaves_the_alternate_screen_when_enter_jumps(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def frames(n):
            return lambda: stream.getvalue().count("\x1b[?2026h") >= n

        def send():
            # open the contents, move down twice, jump to that heading, quit
            for count, key in enumerate((b"t", b"j", b"j"), start=1):
                if not press(master, stream, key, frames(count)):
                    return
            if not press(master, stream, b"\r", lambda: "Inspect 3/3" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        output = stream.getvalue()
        assert output.count(LEAVE) == 1
        assert "lime · doc.md · Inspect 3/3" in output
    finally:
        os.close(master)
        os.close(slave)


def test_run_re_anchors_every_section_jump(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", *["true"] * 8])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            if not press(master, stream, b"n", lambda: "Install 1/3" in stream.getvalue()):
                return
            if not press(master, stream, b"n", lambda: "Configure 2/3" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        # A relative step would desync whenever the viewport moved behind
        # lime's back, so every jump re-anchors at the bottom and counts
        # marks back to the target: 5 marks for 3 sections, start and end.
        assert "scroll_to_bottom" in runner.scripts[2]
        assert "jump_to_prompt:-4" in runner.scripts[2]
        assert "scroll_to_bottom" in runner.scripts[3]
        assert "jump_to_prompt:-3" in runner.scripts[3]
    finally:
        os.close(master)
        os.close(slave)


def test_run_jumps_to_the_absolute_mark_after_an_overlay(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", *["true"] * 8])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            if not press(master, stream, b"n", lambda: "Install 1/3" in stream.getvalue()):
                return
            if not press(master, stream, b"t", lambda: ENTER in stream.getvalue()):
                return
            if not press(master, stream, b"\x1b", lambda: LEAVE in stream.getvalue()):
                return
            if not press(master, stream, b"n", lambda: "Configure 2/3" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        # Opening an overlay cannot leave the next jump pointing at the wrong
        # section: it re-anchors and counts back to section 1 regardless.
        assert "scroll_to_bottom" in runner.scripts[-1]
        assert "jump_to_prompt:-3" in runner.scripts[-1]
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
        run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
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
            # the panel-unrelated nonce/lime titles well before the signal
            # handlers are even installed, which would race a signal sent any
            # earlier and blame normal startup output on resized().
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
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
            if not press(master, stream, b"\x1b", lambda: LEAVE in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
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
            os.write(master, b"t")  # open the contents, then leave them open
            if wait_until(lambda: ENTER in stream.getvalue()):
                os.kill(os.getpid(), signal.SIGTERM)

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 128 + signal.SIGTERM
        assert_restored(slave, original)
        output = stream.getvalue()
        assert output.count(LEAVE) == 1  # the still-open help panel was closed
        assert output.endswith("\x1b[23;2t")
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
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
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
        run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
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
            run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
        assert_restored(slave, original)
        assert stream.getvalue().endswith("\x1b[23;2t")
    finally:
        os.close(master)
        os.close(slave)


def test_missing_controlling_terminal_is_a_quiet_noop(monkeypatch):
    def fake_open(path, flags, *rest):
        if path == "/dev/tty":
            raise OSError("no tty")
        return os.open(path, flags, *rest)

    monkeypatch.setattr(reader.os, "open", fake_open)
    assert run(io.StringIO(), SECTIONS, "doc.md", Terminal(80, 24)) == 0


def test_discovery_title_error_still_restores_handlers_and_closes_the_helper(monkeypatch):
    class FailingStream:
        writes = 0

        def write(self, _text):
            self.writes += 1
            if self.writes > 1:  # title stack save succeeds; discovery title fails
                raise OSError("output broke")

        def flush(self):
            pass

    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner([])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        before = signal.getsignal(signal.SIGWINCH)
        with pytest.raises(OSError, match="output broke"):
            run(FailingStream(), SECTIONS, "doc.md", Terminal(80, 24))
        assert signal.getsignal(signal.SIGWINCH) is before
        assert runner.closed
    finally:
        os.close(master)
        os.close(slave)


def test_run_jumps_to_the_document_start_and_end(monkeypatch):
    # g and G are only covered as pure decisions elsewhere; this pins the jumps
    # they actually issue, which is where a regression would otherwise hide.
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", *["true"] * 8])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def scripts(n):
            return lambda: len(runner.scripts) >= n

        def send():
            # discovery and the opening jump are scripts 0 and 1
            if not press(master, stream, b"n", scripts(3)):
                return
            if not press(master, stream, b"g", scripts(4)):
                return
            if not press(master, stream, b"G", scripts(5)):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        # 3 sections means 5 marks: start, three headings, end.
        assert "jump_to_prompt:-5" in runner.scripts[1]  # opening, the start mark
        assert "jump_to_prompt:-4" in runner.scripts[2]  # n, the first heading
        assert "jump_to_prompt:-5" in runner.scripts[3]  # g, back to the start
        assert "jump_to_prompt:-1" in runner.scripts[4]  # G, the end mark
        assert all("scroll_to_bottom" in script for script in runner.scripts[1:5])
    finally:
        os.close(master)
        os.close(slave)


def test_z_toggles_zen_mode():
    state, action = step(State(mode="idle", selected=0, current=None, zen=False), "z", SECTIONS)
    assert state.zen is True and action is Action.ZEN
    state, action = step(state, "z", SECTIONS)
    assert state.zen is False and action is Action.ZEN


def test_run_accepts_zen_parameter(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), zen=True)
            peer.result(timeout=5)
        assert result == 0
    finally:
        os.close(master)
        os.close(slave)


def test_run_accepts_reprint_callback(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        called = []

        def mock_reprint(zen: bool, columns: int, rows: int) -> None:
            called.append((zen, columns, rows))

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), zen=False, reprint=mock_reprint)
            peer.result(timeout=5)
        assert result == 0
    finally:
        os.close(master)
        os.close(slave)


def test_z_key_triggers_reflow_via_reprint(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        runner = FakeRunner(["SURFACE-1", "true", "true"])
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(runner))
        stream = io.StringIO()
        called = []

        def mock_reprint(zen: bool, columns: int, rows: int) -> None:
            called.append((zen, columns, rows))

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.write(master, b"z")
            time.sleep(0.1)
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, SECTIONS, "doc.md", Terminal(80, 24), zen=False, reprint=mock_reprint)
            peer.result(timeout=5)
        assert result == 0
        assert called  # reprint was called on z keypress
    finally:
        os.close(master)
        os.close(slave)
