"""Ask Ghostty to move its own viewport; never move it from this process."""

from __future__ import annotations

import os
import secrets
import sys
import time
from collections.abc import Callable
from typing import TextIO

from lime.terminal import clean_text

_DEBUG = bool(os.environ.get("LIME_DEBUG"))


def _debug(*parts: object) -> None:
    if _DEBUG:
        print("[lime]", *parts, file=sys.stderr, flush=True)


ATTEMPTS = 3
DELAY = 0.08
START = -1
END = -2
BUNDLE_ID = "com.mitchellh.ghostty"


def title(text: str) -> str:
    """OSC 2 sequence to rename the terminal's window/tab title."""
    return f"\x1b]2;{clean_text(text).replace(chr(10), '')}\x1b\\"


def ghostty_app() -> object | None:
    """A ScriptingBridge handle to Ghostty, or None where scripting is unavailable.

    Apple Events dispatched through this handle cost well under a millisecond, so
    a jump lands in the same frame as the keypress -- unlike shelling out to
    osascript per move, which was slow enough for Ghostty's own
    scroll-to-bottom-on-keystroke to flash first.
    """
    try:
        from ScriptingBridge import SBApplication
    except ImportError:
        return None
    return SBApplication.applicationWithBundleIdentifier_(BUNDLE_ID)


_AUTODETECT = object()


class Bridge:
    """A live handle to this session's Ghostty surface, or a permanently dead one."""

    def __init__(self, app: object | None = _AUTODETECT) -> None:
        self._app = ghostty_app() if app is _AUTODETECT else app
        self.surface: object | None = None
        _debug("Bridge app ->", self._app)

    @property
    def available(self) -> bool:
        return self.surface is not None

    def discover(
        self,
        stream: TextIO,
        settle: Callable[[], object] | None = None,
        pause: Callable[[float], object] = time.sleep,
    ) -> bool:
        """Name this surface uniquely, find it by that name, then rename it.

        Writing the title is not enough: scripting reads Ghostty's UI state, not
        the pty, so the query has to wait until the emulator has caught up.
        """
        if self._app is None:
            _debug("discover -> no Ghostty scripting handle (pyobjc missing?)")
            return False
        nonce = f"lime-{secrets.token_hex(8)}"
        stream.write(title(nonce))
        stream.flush()
        if settle is not None:
            _debug("settle ->", settle())
        try:
            # Settling only proves Ghostty's parser consumed the title. The name
            # the UI reports is updated a beat later, so the query itself is the
            # only thing that can observe the rename landing.
            for attempt in range(ATTEMPTS):
                surface = self._named(nonce)
                if surface is not None:
                    break
                _debug("discovery attempt", attempt + 1, "found nothing")
                pause(DELAY)
            else:
                # The title is shared, mutable state: a shell hook, multiplexer
                # or another TUI can overwrite the nonce before Ghostty is asked
                # about it. Discovery runs at startup, right after the user
                # launched lime, so the focused surface is this one.
                surface = self._focused()
                _debug("rename went unseen; focused surface ->", _surface_id(surface))
        finally:
            stream.write(title("lime"))
            stream.flush()
        self.surface = surface
        _debug("discover ->", "surface", _surface_id(self.surface) or "(none, jumping disabled)")
        return self.available

    def _named(self, nonce: str) -> object | None:
        try:
            found = [(terminal, terminal.name()) for terminal in self._app.terminals()]
        except Exception as error:  # noqa: BLE001 - Ghostty may have quit
            _debug("terminal scan raised:", error)
            return None
        _debug("terminals seen:", [name for _t, name in found], "| want", nonce)
        for terminal, name in found:
            if name == nonce:
                return terminal
        return None

    def _focused(self) -> object | None:
        try:
            for window in self._app.windows():
                terminal = window.selectedTab().focusedTerminal()
                if terminal is not None:
                    return terminal
        except Exception as error:  # noqa: BLE001 - Ghostty may have quit
            _debug("focused lookup raised:", error)
        return None

    def perform(self, action: str) -> bool:
        return self.perform_actions([action])

    def perform_actions(self, actions: list[str]) -> bool:
        """Send a complete movement in one call so no intermediate frame is requested."""
        if not self.available:
            _debug("perform_actions", actions, "-> skipped (bridge unavailable)")
            return False
        # A refused action is not fatal: Ghostty rejects scrolling while the
        # alternate screen is up, for instance, and the next key should still
        # work. There is no second way to move the viewport to fall back to.
        try:
            for action in actions:
                ok = self._app.performAction_on_(action, self.surface)
                _debug("performAction", action, "->", ok)
                if not ok:
                    return False
        except Exception as error:  # noqa: BLE001 - Ghostty may have quit
            _debug("perform_actions raised:", error)
            return False
        return True

    def scroll_to_row(self, row: int) -> bool:
        """Scroll so the given absolute scrollback row sits at the viewport top.

        The reader has cleared the scrollback (CSI 3J) so the document starts at
        row 0; an absolute row lands on a heading in one motion, where Ghostty's
        jump_to_prompt would need a fragile prompt-mark count from an anchor.
        """
        return self.perform_actions([f"scroll_to_row:{max(0, row)}"])

    def scroll_to_bottom(self) -> bool:
        return self.perform("scroll_to_bottom")

    def close(self) -> None:
        self.surface = None


def _surface_id(surface: object | None) -> str | None:
    if surface is None:
        return None
    try:
        return surface.id()
    except Exception:  # noqa: BLE001 - a stale handle
        return None
