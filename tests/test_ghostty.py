import io
from pathlib import Path

import pytest
from rich.console import Console

from lime.ghostty import ATTEMPTS, END, START, Bridge, jump_actions, title
from lime.render import END_MARK, MARK, render


def test_title_is_an_osc_2_sequence():
    assert title("lime · README.md") == "\x1b]2;lime · README.md\x1b\\"


def test_title_strips_control_characters():
    assert title("a\x1bb\nc") == "\x1b]2;abc\x1b\\"


def test_jump_reanchors_then_walks_back_from_the_end():
    assert jump_actions(0, 8) == ["scroll_to_bottom", "jump_to_prompt:-9"]
    assert jump_actions(7, 8) == ["scroll_to_bottom", "jump_to_prompt:-2"]
    assert jump_actions(5, 8) == ["scroll_to_bottom", "jump_to_prompt:-4"]


def test_start_walks_back_past_every_heading_to_the_document_mark():
    assert jump_actions(START, 8) == ["scroll_to_bottom", "jump_to_prompt:-10"]


def test_a_document_with_no_headings_still_has_a_start_mark_to_reach():
    assert jump_actions(START, 0) == ["scroll_to_bottom", "jump_to_prompt:-2"]


def test_end_is_the_last_mark_before_the_reserved_viewport():
    assert jump_actions(END, 8) == ["scroll_to_bottom", "jump_to_prompt:-1"]


@pytest.mark.parametrize(
    "source",
    ["# One\n\n## Two\n", "Intro\n\n# One\n\n" + "body\n\n" * 50 + "## Two\n", "Body\n"],
)
@pytest.mark.parametrize("rows", [10, 24, 60])
def test_every_target_is_reachable_from_the_bottom_viewport(source, rows):
    stream = io.StringIO()
    sections = render(
        source,
        Console(file=stream, width=80, force_terminal=True, color_system="truecolor"),
        base=Path.cwd(),
        marks=True,
    )
    stream.write(END_MARK + "\n" * (rows + 1))
    output = stream.getvalue()
    # Ghostty records one prompt per physical row and scrollPrompt searches
    # strictly ABOVE the top of the viewport (not above the cursor).
    parts = output.split(MARK)
    mark_rows = []
    row = parts[0].count("\n")
    for part in parts[1:]:
        mark_rows.append(row)
        row += part.count("\n")
    assert len(set(mark_rows)) == len(sections) + 2
    viewport_top = max(0, row - rows + 1)
    searchable = sorted({mark for mark in mark_rows if mark < viewport_top}, reverse=True)
    for target, expected in [(START, mark_rows[0]), (END, mark_rows[-1]),
                             *[(s.index, mark_rows[s.index + 1]) for s in sections]]:
        delta = int(jump_actions(target, len(sections))[-1].split(":")[1])
        assert searchable[-delta - 1] == expected


def test_no_jump_is_ever_a_negative_zero():
    for total in range(6):
        for index in [START, *range(total)]:
            assert "-0" not in jump_actions(index, total)[1]


class FakeRunner:
    def __init__(self, replies):
        self.replies = list(replies)
        self.scripts = []

    def __call__(self, script):
        self.scripts.append(script)
        return self.replies.pop(0) if self.replies else "false"

    def close(self):
        self.closed = True


def test_discovery_sets_a_nonce_then_restores_the_title():
    stream = io.StringIO()
    runner = FakeRunner(["SURFACE-1"])
    bridge = Bridge(runner)
    assert bridge.discover(stream) is True
    assert bridge.available
    written = stream.getvalue()
    assert written.startswith("\x1b]2;lime-") and written.count("\x1b]2;") == 2
    assert "SURFACE-1" not in written


def test_discovery_failure_disables_jumping():
    bridge = Bridge(FakeRunner([""] * (ATTEMPTS + 1)))
    assert bridge.discover(io.StringIO(), pause=lambda _: None) is False
    assert not bridge.available


def test_discovery_restores_the_title_even_when_the_runner_raises():
    class RaisingRunner(FakeRunner):
        def __call__(self, script):
            self.scripts.append(script)
            raise RuntimeError("boom")

    stream = io.StringIO()
    bridge = Bridge(RaisingRunner([]))
    try:
        bridge.discover(stream)
    except RuntimeError:
        pass
    written = stream.getvalue()
    assert written.startswith("\x1b]2;lime-") and written.count("\x1b]2;") == 2


def test_jump_performs_both_actions_against_the_surface():
    runner = FakeRunner(["SURFACE-1", "true", "true"])
    bridge = Bridge(runner)
    bridge.discover(io.StringIO())
    assert bridge.jump(2, 5) is True
    assert "scroll_to_bottom" in runner.scripts[1]
    assert "jump_to_prompt:-4" in runner.scripts[1]
    assert "SURFACE-1" in runner.scripts[1]
    assert len(runner.scripts) == 2  # discovery and one complete movement


def test_discovery_searches_all_terminals_including_unfocused_splits():
    runner = FakeRunner(["SURFACE-1"])
    Bridge(runner).discover(io.StringIO())
    assert "repeat with s in terminals" in runner.scripts[0]
    assert "focused terminal" not in runner.scripts[0]


def test_a_refused_action_leaves_the_bridge_usable():
    # Ghostty refuses scrolling while the alternate screen is up, and there is
    # no second way to move the viewport, so the next key must still work.
    runner = FakeRunner(["SURFACE-1", "false", "true"])
    bridge = Bridge(runner)
    bridge.discover(io.StringIO())
    assert bridge.jump(0, 3) is False
    assert bridge.available
    assert bridge.jump(0, 3) is True


def test_jump_without_discovery_is_false():
    assert Bridge(FakeRunner([])).jump(0, 3) is False


def test_close_is_safe_before_discovery():
    Bridge(FakeRunner([])).close()


def test_discovery_retries_until_the_rename_reaches_ghostty():
    # The title is parsed well before AppleScript can see the new name, so the
    # first query legitimately comes back empty.
    runner = FakeRunner(["", "", "SURFACE-1"])
    bridge = Bridge(runner)
    assert bridge.discover(io.StringIO(), pause=lambda _: None) is True
    assert bridge.surface == "SURFACE-1"
    assert len(runner.scripts) == 3


def test_discovery_gives_up_after_a_bounded_number_of_attempts():
    runner = FakeRunner([""] * (ATTEMPTS * 2))
    bridge = Bridge(runner)
    assert bridge.discover(io.StringIO(), pause=lambda _: None) is False
    assert len(runner.scripts) == ATTEMPTS + 1  # the retries, then the fallback


def test_discovery_settles_the_terminal_before_asking_ghostty():
    # AppleScript reads Ghostty's state, not the pty: querying before the
    # emulator has parsed the title finds no surface at all.
    order = []
    runner = FakeRunner(["SURFACE-1"])

    def record(script):
        order.append("query")
        return runner(script)

    bridge = Bridge(record)
    assert bridge.discover(io.StringIO(), settle=lambda: order.append("settle")) is True
    assert order == ["settle", "query"]


def test_discovery_without_a_settle_still_works():
    bridge = Bridge(FakeRunner(["SURFACE-1"]))
    assert bridge.discover(io.StringIO()) is True


def test_discovery_falls_back_to_the_focused_surface_when_the_rename_is_unseen():
    # A shell hook, multiplexer or another TUI can overwrite the title before
    # Ghostty is asked about it, which no amount of retrying will fix.
    runner = FakeRunner([*[""] * ATTEMPTS, "SURFACE-9"])
    bridge = Bridge(runner)
    assert bridge.discover(io.StringIO(), pause=lambda _: None) is True
    assert bridge.surface == "SURFACE-9"
    assert "focused terminal of selected tab of front window" in runner.scripts[-1]


def test_a_successful_rename_never_consults_the_focused_surface():
    runner = FakeRunner(["SURFACE-1"])
    bridge = Bridge(runner)
    assert bridge.discover(io.StringIO(), pause=lambda _: None) is True
    assert len(runner.scripts) == 1
    assert all("focused terminal" not in script for script in runner.scripts)
