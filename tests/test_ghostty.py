import io

from lime.ghostty import START, Bridge, jump_actions, title


def test_title_is_an_osc_2_sequence():
    assert title("lime · README.md") == "\x1b]2;lime · README.md\x1b\\"


def test_title_strips_control_characters():
    assert title("a\x1bb\nc") == "\x1b]2;abc\x1b\\"


def test_jump_reanchors_then_walks_back_from_the_end():
    assert jump_actions(0, 8) == ["scroll_to_bottom", "jump_to_prompt:-8"]
    assert jump_actions(7, 8) == ["scroll_to_bottom", "jump_to_prompt:-1"]
    assert jump_actions(5, 8) == ["scroll_to_bottom", "jump_to_prompt:-3"]


def test_start_walks_back_past_every_heading_to_the_document_mark():
    assert jump_actions(START, 8) == ["scroll_to_bottom", "jump_to_prompt:-9"]


def test_a_document_with_no_headings_still_has_a_start_mark_to_reach():
    assert jump_actions(START, 0) == ["scroll_to_bottom", "jump_to_prompt:-1"]


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
    bridge = Bridge(FakeRunner([""]))
    assert bridge.discover(io.StringIO()) is False
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
    assert "jump_to_prompt:-3" in runner.scripts[2]
    assert "SURFACE-1" in runner.scripts[2]


def test_a_failed_action_disables_the_bridge_permanently():
    runner = FakeRunner(["SURFACE-1", "false"])
    bridge = Bridge(runner)
    bridge.discover(io.StringIO())
    assert bridge.jump(1, 5) is False
    assert not bridge.available
    assert bridge.jump(2, 5) is False
    assert len(runner.scripts) == 2  # no retry after the failure


def test_jump_without_discovery_is_false():
    assert Bridge(FakeRunner([])).jump(0, 3) is False


def test_close_is_safe_before_discovery():
    Bridge(FakeRunner([])).close()
