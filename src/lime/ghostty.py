"""Ask Ghostty to move its own viewport; never move it from this process."""

from __future__ import annotations

import secrets
import shutil
import subprocess
from typing import TextIO

from lime.terminal import clean_text

START = -1
RUNNER_UNAVAILABLE = "reprint"
FIND = """
tell application "Ghostty"
  repeat with w in windows
    repeat with t in tabs of w
      set s to focused terminal of t
      if name of s is "{nonce}" then return id of s
    end repeat
  end repeat
end tell
return ""
"""
ACT = """
tell application "Ghostty"
  set s to first terminal whose id is "{surface}"
  return perform action "{action}" on s
end tell
"""


def title(text: str) -> str:
    """OSC 2 sequence to rename the terminal's window/tab title."""
    return f"\x1b]2;{clean_text(text).replace(chr(10), '')}\x1b\\"


def jump_actions(index: int, total: int) -> list[str]:
    """Anchor at the bottom, then walk back to one mark; total counts headings.

    Marks are 0 for the document's start and i + 1 for heading i, so a document
    with n headings carries n + 1 of them. Anchoring first makes the walk
    independent of wherever the reader has scrolled by hand.
    """
    marks = total + 1
    mark = 0 if index == START else index + 1
    return ["scroll_to_bottom", f"jump_to_prompt:-{marks - mark}"]


def osascript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


class Bridge:
    """A live handle to this session's Ghostty surface, or a permanently dead one."""

    def __init__(self, runner=None) -> None:
        self.runner = runner or (osascript if shutil.which("osascript") else None)
        self.surface: str | None = None
        self.failed = False

    @property
    def available(self) -> bool:
        return bool(self.surface) and not self.failed

    def discover(self, stream: TextIO) -> bool:
        """Name this surface uniquely, find it by that name, then rename it."""
        if self.runner is None:
            return False
        nonce = f"lime-{secrets.token_hex(8)}"
        stream.write(title(nonce))
        stream.flush()
        try:
            surface = self.runner(FIND.format(nonce=nonce))
        finally:
            stream.write(title("lime"))
            stream.flush()
        self.surface = surface or None
        return self.available

    def perform(self, action: str) -> bool:
        if not self.available:
            return False
        if self.runner(ACT.format(surface=self.surface, action=action)) != "true":
            # One bad action means the bridge is gone; retrying every keypress
            # would feel broken, so fall back for the rest of the session.
            self.failed = True
            return False
        return True

    def jump(self, index: int, total: int) -> bool:
        return all(self.perform(action) for action in jump_actions(index, total))

    def close(self) -> None:
        if self.runner is not None and hasattr(self.runner, "close"):
            self.runner.close()
