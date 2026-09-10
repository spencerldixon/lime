import io
import re

from lime.ghostty import ATTEMPTS, Bridge, title


def test_title_is_an_osc_2_sequence():
    assert title("lime · README.md") == "\x1b]2;lime · README.md\x1b\\"


def test_title_strips_control_characters():
    assert title("a\x1bb\nc") == "\x1b]2;abc\x1b\\"


class FakeTerminal:
    def __init__(self, ident, name):
        self._id, self._name = ident, name

    def id(self):
        return self._id

    def name(self):
        return self._name


class FakeTab:
    def __init__(self, terminal):
        self._terminal = terminal

    def focusedTerminal(self):
        return self._terminal


class FakeWindow:
    def __init__(self, terminal):
        self._terminal = terminal

    def selectedTab(self):
        return FakeTab(self._terminal)


class FakeGhostty:
    """Stands in for the ScriptingBridge handle to Ghostty.

    `discover()` renames the tab to a random nonce by writing an OSC 2 to the
    stream; this reads that nonce back out of the same stream and, from the
    `rename_after`-th scan on, reports one terminal carrying it. `raises` makes
    the terminal scan blow up, `refuse` makes performAction return False.
    """

    def __init__(self, stream, *, rename_after=0, focused=None, raises=False, refuse=False):
        self._stream = stream
        self._rename_after = rename_after
        self._focused = focused
        self._raises = raises
        self._refuse = refuse
        self.scans = 0
        self.actions = []  # (action, surface id) for every performAction call

    def _nonce(self):
        found = re.findall(r"\x1b\]2;(lime-[0-9a-f]+)\x1b\\", self._stream.getvalue())
        return found[-1] if found else None

    def terminals(self):
        if self._raises:
            raise RuntimeError("Ghostty went away")
        self.scans += 1
        nonce = self._nonce()
        name = nonce if (nonce and self.scans > self._rename_after) else "shell"
        return [FakeTerminal("SURFACE-1", name), FakeTerminal("OTHER", "other")]

    def windows(self):
        return [FakeWindow(FakeTerminal(self._focused, "focused"))] if self._focused else []

    def performAction_on_(self, action, terminal):
        self.actions.append((action, terminal.id()))
        return not self._refuse


def discovered(**kwargs):
    """A Bridge already through discovery, plus its FakeGhostty."""
    stream = io.StringIO()
    app = FakeGhostty(stream, **kwargs)
    bridge = Bridge(app)
    bridge.discover(stream, pause=lambda _s: None)
    return bridge, app


def test_discovery_sets_a_nonce_then_restores_the_title():
    stream = io.StringIO()
    app = FakeGhostty(stream)
    bridge = Bridge(app)
    assert bridge.discover(stream, pause=lambda _s: None) is True
    assert bridge.available
    written = stream.getvalue()
    assert written.startswith("\x1b]2;lime-") and written.count("\x1b]2;") == 2
    assert written.endswith(title("lime"))


def test_discovery_failure_disables_jumping():
    # rename_after past every attempt: the nonce is never seen, no focused window.
    bridge, _ = discovered(rename_after=ATTEMPTS + 5)
    assert not bridge.available


def test_discovery_restores_the_title_even_when_the_scan_raises():
    stream = io.StringIO()
    bridge = Bridge(FakeGhostty(stream, raises=True))
    bridge.discover(stream, pause=lambda _s: None)
    assert stream.getvalue().endswith(title("lime"))


def test_no_ghostty_handle_means_no_discovery():
    assert Bridge(None).discover(io.StringIO()) is False


def test_scroll_to_row_performs_the_action_against_the_surface():
    bridge, app = discovered()
    assert bridge.scroll_to_row(42) is True
    assert app.actions == [("scroll_to_row:42", "SURFACE-1")]


def test_scroll_to_row_never_goes_negative():
    bridge, app = discovered()
    assert bridge.scroll_to_row(-5) is True
    assert app.actions[-1] == ("scroll_to_row:0", "SURFACE-1")


def test_scroll_to_bottom_is_a_single_action():
    bridge, app = discovered()
    assert bridge.scroll_to_bottom() is True
    assert app.actions == [("scroll_to_bottom", "SURFACE-1")]


def test_perform_actions_stops_at_the_first_refusal():
    bridge, app = discovered(refuse=True)
    assert bridge.perform_actions(["scroll_to_top", "scroll_to_bottom"]) is False
    assert app.actions == [("scroll_to_top", "SURFACE-1")]  # the second never ran


def test_a_refused_action_leaves_the_bridge_usable():
    bridge, _ = discovered(refuse=True)
    assert bridge.scroll_to_row(0) is False
    assert bridge.available  # the surface handle is still good for the next key


def test_a_scan_that_raises_mid_session_is_not_fatal():
    bridge, app = discovered()
    app._raises = True  # Ghostty quits after discovery
    assert bridge.scroll_to_row(0) is True  # performAction still answers
    app._refuse = True
    assert bridge.scroll_to_row(0) is False


def test_scrolling_without_discovery_is_false():
    stream = io.StringIO()
    assert Bridge(FakeGhostty(stream)).scroll_to_row(0) is False


def test_close_is_safe_before_discovery():
    Bridge(FakeGhostty(io.StringIO())).close()


def test_close_drops_the_surface():
    bridge, _ = discovered()
    bridge.close()
    assert not bridge.available


def test_discovery_retries_until_the_rename_reaches_ghostty():
    # The title is parsed well before the UI reports the new name, so the first
    # scans legitimately come back without it.
    bridge, app = discovered(rename_after=2)
    assert bridge.available
    assert bridge.surface.id() == "SURFACE-1"
    assert app.scans == 3


def test_discovery_gives_up_after_a_bounded_number_of_attempts():
    stream = io.StringIO()
    app = FakeGhostty(stream, rename_after=99)
    bridge = Bridge(app)
    assert bridge.discover(stream, pause=lambda _s: None) is False
    assert app.scans == ATTEMPTS  # then the focused-window fallback, which is empty


def test_discovery_settles_the_terminal_before_asking_ghostty():
    stream = io.StringIO()
    order = []
    app = FakeGhostty(stream)
    real_terminals = app.terminals

    def record():
        order.append("query")
        return real_terminals()

    app.terminals = record
    bridge = Bridge(app)
    assert bridge.discover(stream, settle=lambda: order.append("settle")) is True
    assert order[:2] == ["settle", "query"]


def test_discovery_without_a_settle_still_works():
    stream = io.StringIO()
    bridge = Bridge(FakeGhostty(stream))
    assert bridge.discover(stream, pause=lambda _s: None) is True


def test_discovery_falls_back_to_the_focused_surface_when_the_rename_is_unseen():
    # A shell hook, multiplexer or another TUI can overwrite the title before
    # Ghostty is asked about it, which no amount of retrying will fix.
    bridge, _ = discovered(rename_after=99, focused="SURFACE-9")
    assert bridge.available
    assert bridge.surface.id() == "SURFACE-9"


def test_a_successful_rename_never_consults_the_focused_surface():
    bridge, _ = discovered(rename_after=0, focused="SURFACE-9")
    assert bridge.surface.id() == "SURFACE-1"  # not the focused fallback
