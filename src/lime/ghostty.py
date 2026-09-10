"""Ask Ghostty to move its own viewport; never move it from this process."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
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
FIND = """
tell application "Ghostty"
  repeat with s in terminals
    if name of s is "{nonce}" then return id of s
  end repeat
end tell
return ""
"""
FOCUSED = """
tell application "Ghostty"
  return id of focused terminal of selected tab of front window
end tell
"""
ACT = """
tell application "Ghostty"
  set s to first terminal whose id is "{surface}"
  {commands}
  return true
end tell
"""


def title(text: str) -> str:
    """OSC 2 sequence to rename the terminal's window/tab title."""
    return f"\x1b]2;{clean_text(text).replace(chr(10), '')}\x1b\\"


def jump_actions(index: int, total: int) -> list[str]:
    """Anchor at the bottom, then walk back to one mark; total counts headings.

    Marks are 0 for the start, i + 1 for heading i, and n + 1 for the end.
    The CLI reserves a blank viewport after the end before entering the reader.
    Anchoring first makes the walk independent of manual scrolling.
    """
    # The CLI adds an end mark and a blank viewport, keeping every mark above
    # the bottom viewport's first row, where Ghostty begins counting upwards.
    marks = total + 2
    mark = total + 1 if index == END else 0 if index == START else index + 1
    return ["scroll_to_bottom", f"jump_to_prompt:-{marks - mark}"]


def osascript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        _debug("osascript raised:", error)
        return ""
    out = result.stdout.strip()
    _debug(f"osascript rc={result.returncode} out={out!r} err={result.stderr.strip()!r}")
    return out if result.returncode == 0 else ""


class Bridge:
    """A live handle to this session's Ghostty surface, or a permanently dead one."""

    def __init__(self, runner=None) -> None:
        self.runner = runner or (osascript if shutil.which("osascript") else None)
        self.surface: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.surface)

    def discover(
        self,
        stream: TextIO,
        settle: Callable[[], object] | None = None,
        pause: Callable[[float], object] = time.sleep,
    ) -> bool:
        """Name this surface uniquely, find it by that name, then rename it.

        Writing the title is not enough: AppleScript reads Ghostty's state, not
        the pty, so the query has to wait until the emulator has caught up.
        """
        if self.runner is None:
            return False
        nonce = f"lime-{secrets.token_hex(8)}"
        stream.write(title(nonce))
        stream.flush()
        if settle is not None:
            _debug("settle ->", settle())
        try:
            # Settling only proves Ghostty's parser consumed the title. The name
            # AppleScript reads is updated a beat later by the UI, so the query
            # itself is the only thing that can observe the rename landing.
            for attempt in range(ATTEMPTS):
                surface = self.runner(FIND.format(nonce=nonce))
                if surface:
                    break
                _debug("discovery attempt", attempt + 1, "found nothing")
                pause(DELAY)
            else:
                # The title is shared, mutable state: a shell hook, multiplexer
                # or another TUI can overwrite the nonce before Ghostty is asked
                # about it. Discovery runs at startup, right after the user
                # launched lime, so the focused surface is this one.
                surface = self.runner(FOCUSED)
                _debug("rename went unseen; focused surface ->", surface or "(none)")
        finally:
            stream.write(title("lime"))
            stream.flush()
        self.surface = surface or None
        _debug("discover ->", "surface", self.surface or "(none, jumping disabled)")
        return self.available

    def perform(self, action: str) -> bool:
        return self.perform_actions([action])

    def perform_actions(self, actions: list[str]) -> bool:
        """Send a complete movement in one call so no intermediate frame is requested."""
        if not self.available:
            _debug("perform_actions", actions, "-> skipped (bridge unavailable)")
            return False
        commands = "\n  ".join(
            f'if not (perform action "{action}" on s) then return false'
            for action in actions
        )
        _debug("perform_actions", actions)
        # A refused action is not fatal: Ghostty rejects scrolling while the
        # alternate screen is up, for instance, and the next key should still
        # work. There is no second way to move the viewport to fall back to.
        return self.runner(ACT.format(surface=self.surface, commands=commands)) == "true"

    def jump(self, index: int, total: int) -> bool:
        return self.perform_actions(jump_actions(index, total))

    def close(self) -> None:
        if self.runner is not None and hasattr(self.runner, "close"):
            self.runner.close()
