import fcntl
import io
import os
import signal
import struct
import termios
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_ghostty import FakeGhostty
from test_terminal import assert_restored

from lime import ghostty, reader
from lime.ghostty import Bridge
from lime.keys import Key
from lime.outline import Section
from lime.overlay import ENTER, LEAVE
from lime.reader import Action, Layout, State, bar, run, step
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



def test_question_mark_opens_help():
    state, action = step(IDLE, "?", SECTIONS)
    assert state.mode == "help" and action is Action.OPEN

@pytest.mark.parametrize("state", [State("toc"), State("idle"), State("help")])
def test_ctrl_d_quits_from_every_overlay(state):
    assert step(state, Key.EOF, SECTIONS)[1] is Action.QUIT


@pytest.mark.parametrize("key", ["x", Key.ESCAPE, "q"])
def test_any_key_closes_help(key):
    state, action = step(State("help"), key, SECTIONS)
    assert state.mode == "idle" and action is Action.CLOSE


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


def test_ctrl_n_and_ctrl_p_move_the_selection():
    state = State("toc", 0, None)
    assert step(state, Key.NEXT, SECTIONS)[0].selected == 1
    assert step(State("toc", 2, None), Key.PREVIOUS, SECTIONS)[0].selected == 1


def test_the_selection_stops_at_both_ends_rather_than_wrapping():
    assert step(State("toc", 0, None), Key.UP, SECTIONS)[0].selected == 0
    assert step(State("toc", 2, None), Key.DOWN, SECTIONS)[0].selected == 2


def test_typing_a_printable_key_extends_the_filter_and_resets_the_selection():
    state, action = step(State("toc", 2, None), "i", SECTIONS)
    assert state.query == "i" and state.selected == 0 and action is Action.REDRAW
    state, _ = step(state, "n", SECTIONS)
    assert state.query == "in"


def test_backspace_trims_the_filter_and_is_inert_when_it_is_empty():
    state, action = step(State("toc", 1, None, "ins"), Key.BACKSPACE, SECTIONS)
    assert state.query == "in" and state.selected == 0 and action is Action.REDRAW
    assert step(State("toc", 0, None), Key.BACKSPACE, SECTIONS) == (State("toc", 0, None), None)


def test_the_selection_clamps_to_the_filtered_match_count():
    # "Inspect" is the only match, so a down press cannot move past row 0.
    assert step(State("toc", 0, None, "inspect"), Key.DOWN, SECTIONS)[0].selected == 0


def test_enter_jumps_to_the_selected_heading_and_closes():
    state, action = step(State("toc", 2, None), Key.ENTER, SECTIONS)
    assert action is Action.JUMP and state.current == 2 and state.mode == "idle"


def test_enter_jumps_to_the_filtered_match_by_its_outline_index():
    # The filter leaves "Configure" as the single match; jumping targets its
    # real outline index, not the row it happens to sit on.
    state, action = step(State("toc", 0, None, "config"), Key.ENTER, SECTIONS)
    assert action is Action.JUMP and state.current == 1 and state.query == ""


def test_enter_with_no_matches_closes_without_jumping():
    state, action = step(State("toc", 0, None, "zzz"), Key.ENTER, SECTIONS)
    assert action is Action.CLOSE and state.mode == "idle" and state.query == ""


def test_enter_in_a_document_without_headings_just_closes():
    state, action = step(State("toc", 0, None), Key.ENTER, [])
    assert action is Action.CLOSE and state.mode == "idle"


def test_escape_closes_without_moving_and_drops_the_filter():
    state, action = step(State("toc", 1, 0, "ins"), Key.ESCAPE, SECTIONS)
    assert state.mode == "idle" and state.current == 0 and state.query == ""
    assert action is Action.CLOSE


def test_ctrl_c_also_closes_the_contents():
    state, action = step(State("toc", 1, 0, "ins"), Key.INTERRUPT, SECTIONS)
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
# tests redirect both: /dev/tty onto a pty we control, and Bridge onto a fake
# ScriptingBridge handle that never touches Apple Events.


def fake_ghostty(monkeypatch, **kwargs):
    """Wire reader.Bridge to a FakeGhostty. Returns (stream, app); pass the same
    stream to run()."""
    stream = io.StringIO()
    app = FakeGhostty(stream, **kwargs)
    monkeypatch.setattr(reader, "Bridge", lambda: Bridge(app))
    return stream, app


def moves(app):
    """Just the action strings from an app's performAction calls, in order."""
    return [action for action, _surface in app.actions]


def steps(app):
    """moves(app) with consecutive repeats (the post-jump settle re-asserts) dropped."""
    seen = []
    for action in moves(app):
        if not seen or seen[-1] != action:
            seen.append(action)
    return seen


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
    if len(args) >= 2 and isinstance(args[1], list):
        args = (args[0], Layout(args[1]), *args[2:])

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
        stream, app = fake_ghostty(monkeypatch)
        os.write(master, b"q")
        result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 12, 40, 88]),
                                  "README.md", Terminal(80, 24))
        assert result == 0
        # Discovery, then the opening scroll to the document top, before any key.
        assert moves(app)[0] == "scroll_to_row:0"
        # The opening scroll lands nowhere sections-shaped, so the bar stays bare.
        assert "lime · README.md" in stream.getvalue()
    finally:
        os.close(master)
        os.close(slave)


def test_run_opens_at_the_top_with_no_headings(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch)
        os.write(master, b"q")
        result = run_with_timeout(5, stream, Layout([], [0]), "README.md", Terminal(80, 24))
        assert result == 0
        assert moves(app)[0] == "scroll_to_row:0"
    finally:
        os.close(master)
        os.close(slave)


def test_discovery_failure_leaves_the_reader_running_without_scrolling(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch, rename_after=99)
        os.write(master, b"q")
        assert run_with_timeout(5, stream, SECTIONS, "README.md", Terminal(80, 24)) == 0
        assert not app.actions
    finally:
        os.close(master)
        os.close(slave)


def test_run_restores_terminal_and_title_on_quit(monkeypatch):
    master, slave = open_pty()
    original = termios.tcgetattr(slave)
    try:
        patch_tty(monkeypatch, slave)
        stream, _app = fake_ghostty(monkeypatch)
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
        stream, _app = fake_ghostty(monkeypatch)

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
        stream, _app = fake_ghostty(monkeypatch)

        def frames(n):
            return lambda: stream.getvalue().count("\x1b[?2026h") >= n

        def send():
            # open the contents, move down twice (Ctrl-N), jump to that heading, quit
            for count, key in enumerate((b"t", b"\x0e", b"\x0e"), start=1):
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


def test_run_scrolls_to_each_section_row(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch)

        def send():
            if not press(master, stream, b"n", lambda: "Install 1/3" in stream.getvalue()):
                return
            if not press(master, stream, b"n", lambda: "Configure 2/3" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 12, 40, 88]),
                                      "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        # anchors[1] and anchors[2] are section 0 and section 1.
        assert steps(app)[1] == "scroll_to_row:12"
        assert steps(app)[2] == "scroll_to_row:40"
    finally:
        os.close(master)
        os.close(slave)


def test_a_section_jump_is_correct_after_an_overlay(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch)

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
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 12, 40, 88]),
                                      "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        # Opening an overlay cannot leave the next jump pointing at the wrong
        # section: it is still an absolute scroll to section 1's row.
        assert moves(app)[-1] == "scroll_to_row:40"
    finally:
        os.close(master)
        os.close(slave)


def test_run_restores_the_previous_sigwinch_handler(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, _app = fake_ghostty(monkeypatch)
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
        stream, _app = fake_ghostty(monkeypatch)
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
        stream, _app = fake_ghostty(monkeypatch)

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
        stream, _app = fake_ghostty(monkeypatch)

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
        stream, _app = fake_ghostty(monkeypatch)
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

        def boom(state, key, sections):
            raise ValueError("boom")

        monkeypatch.setattr(reader, "step", boom)
        stream, _app = fake_ghostty(monkeypatch)
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


def test_a_write_failure_during_startup_restores_the_signal_handlers(monkeypatch):
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
        monkeypatch.setattr(reader, "Bridge", lambda: Bridge(FakeGhostty(io.StringIO())))
        before = signal.getsignal(signal.SIGWINCH)
        with pytest.raises(OSError, match="output broke"):
            run(FailingStream(), Layout(SECTIONS), "doc.md", Terminal(80, 24))
        assert signal.getsignal(signal.SIGWINCH) is before
    finally:
        os.close(master)
        os.close(slave)


def test_run_jumps_to_the_document_start_and_end(monkeypatch):
    # g and G are only covered as pure decisions elsewhere; this pins the moves
    # they actually issue, which is where a regression would otherwise hide.
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch)

        def acted(n):
            return lambda: len(app.actions) >= n

        def send():
            # the opening scroll is move 1; each key adds one more
            if not press(master, stream, b"n", acted(2)):
                return
            if not press(master, stream, b"g", acted(3)):
                return
            if not press(master, stream, b"G", acted(4)):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 7, 20, 55]),
                                      "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        assert steps(app)[0] == "scroll_to_row:0"   # opening, the document top
        assert steps(app)[1] == "scroll_to_row:7"   # n, section 0's row
        assert steps(app)[2] == "scroll_to_row:0"   # g, back to the top
        assert steps(app)[3] == "scroll_to_bottom"  # G, the foot
    finally:
        os.close(master)
        os.close(slave)


# --- resize reflow: clear + reprint, then absolute scroll_to_row navigation ---


def _reprinting(stream, sections, anchors=(0, 3, 40, 90)):
    """A reprint callback that records its calls and marks the stream."""
    calls = []

    def reprint(columns, rows):
        calls.append((columns, rows))
        stream.write("<<REPRINT>>")
        return Layout(list(sections), list(anchors))

    reprint.calls = calls
    return reprint


def test_a_resize_clears_the_scrollback_and_reprints_once(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, _app = fake_ghostty(monkeypatch)
        reprint = _reprinting(stream, SECTIONS)

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.kill(os.getpid(), signal.SIGWINCH)
            if not wait_until(lambda: "<<REPRINT>>" in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 3, 40, 90]),
                                      "doc.md", Terminal(80, 24), reprint=reprint)
            peer.result(timeout=5)
        assert result == 0
        output = stream.getvalue()
        assert output.count("<<REPRINT>>") == 1  # a burst coalesces to one reflow
        assert "\x1b[2J\x1b[3J" in output  # screen + scrollback erased first
        assert output.index("\x1b[2J\x1b[3J") < output.index("<<REPRINT>>")
        assert reprint.calls == [(80, 24)]
    finally:
        os.close(master)
        os.close(slave)


def test_navigation_after_a_reflow_uses_absolute_rows(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch)
        reprint = _reprinting(stream, SECTIONS, anchors=(0, 5, 44, 91))

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.kill(os.getpid(), signal.SIGWINCH)
            if not wait_until(lambda: "<<REPRINT>>" in stream.getvalue()):
                return
            app.actions.clear()
            if not press(master, stream, b"n", lambda: any("scroll_to_row" in a for a in moves(app))):
                return
            os.write(master, b"g")
            if not wait_until(lambda: sum("scroll_to_row" in a for a in moves(app)) >= 2):
                return
            os.write(master, b"G")
            if not wait_until(lambda: any("scroll_to_bottom" in a for a in moves(app))):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 5, 44, 91]),
                                      "doc.md", Terminal(80, 24), reprint=reprint)
            peer.result(timeout=5)
        assert result == 0
        joined = "\n".join(moves(app))
        assert "jump_to_prompt" not in joined  # marks are abandoned after a reflow
        assert "scroll_to_row:5" in joined  # n -> first heading, at anchor[1]
        assert "scroll_to_row:0" in joined  # g -> the document top
        assert "scroll_to_bottom" in joined  # G -> the foot
    finally:
        os.close(master)
        os.close(slave)


def test_a_resize_under_a_panel_reflows_when_it_closes(monkeypatch):
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, _app = fake_ghostty(monkeypatch)
        reprint = _reprinting(stream, SECTIONS)
        observed = {}

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.write(master, b"t")
            if not wait_until(lambda: ENTER in stream.getvalue()):
                return
            marker = len(stream.getvalue())
            os.kill(os.getpid(), signal.SIGWINCH)
            if not wait_until(lambda: len(stream.getvalue()) > marker):
                return
            observed["reprinted_while_open"] = "<<REPRINT>>" in stream.getvalue()
            if not press(master, stream, b"\x1b", lambda: LEAVE in stream.getvalue()):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 3, 40, 90]),
                                      "doc.md", Terminal(80, 24), reprint=reprint)
            peer.result(timeout=5)
        assert result == 0
        output = stream.getvalue()
        assert observed["reprinted_while_open"] is False  # deferred while covered
        assert output.count("<<REPRINT>>") == 1  # then reflowed once, on close
        assert output.index(LEAVE) < output.index("<<REPRINT>>")
    finally:
        os.close(master)
        os.close(slave)


def test_a_jump_is_re_asserted_to_survive_ghosttys_scroll_to_bottom(monkeypatch):
    # Ghostty scrolls to the bottom a frame after a keypress and undoes lime's
    # jump; lime re-issues the same scroll a few times over the next ~60ms.
    master, slave = open_pty()
    try:
        patch_tty(monkeypatch, slave)
        stream, app = fake_ghostty(monkeypatch)

        def send():
            if not wait_until(lambda: "lime · doc.md" in stream.getvalue()):
                return
            os.write(master, b"n")
            # the immediate jump plus its re-asserts all target section 0's row
            if not wait_until(lambda: moves(app).count("scroll_to_row:7") >= 3):
                return
            os.write(master, b"q")

        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(send)
            result = run_with_timeout(5, stream, Layout(SECTIONS, [0, 7, 20, 55]),
                                      "doc.md", Terminal(80, 24))
            peer.result(timeout=5)
        assert result == 0
        assert moves(app).count("scroll_to_row:7") >= 3  # jump + re-asserts
        assert steps(app) == ["scroll_to_row:0", "scroll_to_row:7"]  # opening, then the jump
    finally:
        os.close(master)
        os.close(slave)
