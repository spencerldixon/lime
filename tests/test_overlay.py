import re

from lime.outline import Section
from lime.overlay import ENTER, LEAVE, contents, screen, shortcuts, visible_width, window

SECTIONS = [Section("Installation", 2, 0, 0), Section("Install with Homebrew", 3, 1, 4)]
MANY = [Section(f"Section {n}", 2, n, n) for n in range(50)]


def _plain(row: str) -> str:
    """A rendered row with its SGR removed, so column indexes are real columns."""
    return re.sub(r"\x1b\[[0-9;]*m", "", row)


def _rows(output: str) -> list[str]:
    """The rendered rows of a panel, stripped of the sync wrapper and home move."""
    rows = output.split("\r\n")
    rows[0] = rows[0].removeprefix("\x1b[?2026h\x1b[H")
    rows[-1] = rows[-1].removesuffix("\x1b[?2026l")
    return [row.removesuffix("\x1b[K") for row in rows]


def test_entering_uses_the_alternate_screen_and_hides_the_cursor():
    assert ENTER == "\x1b[?1049h\x1b[?25l"
    assert LEAVE == "\x1b[?25h\x1b[?1049l"


def test_screen_paints_from_the_top_left():
    assert screen(["one"], 3).startswith("\x1b[?2026h\x1b[H")


def test_screen_fills_exactly_the_window_height():
    assert len(_rows(screen(["one", "two"], 6))) == 6
    assert len(_rows(screen(["one"] * 9, 4))) == 4


def test_contents_is_wrapped_in_synchronized_output():
    output = contents(SECTIONS, 0, 50, 40)
    assert output.startswith("\x1b[?2026h") and output.endswith("\x1b[?2026l")


def test_contents_fills_the_whole_window():
    assert len(_rows(contents(SECTIONS, 0, 50, 40))) == 40
    assert len(_rows(contents(MANY, 0, 50, 12))) == 12


def test_contents_shows_every_heading_when_they_fit():
    output = contents(SECTIONS, 0, 50, 40)
    assert "Installation" in output and "Install with Homebrew" in output


def test_contents_highlights_the_selected_row():
    first = contents(SECTIONS, 0, 50, 40)
    second = contents(SECTIONS, 1, 50, 40)
    assert next(row for row in _rows(first) if "Installation" in row).startswith("\x1b[7m")
    assert next(row for row in _rows(second) if "Homebrew" in row).startswith("\x1b[7m")
    assert not next(row for row in _rows(second) if "Installation" in row).startswith("\x1b[7m")


def test_the_highlight_spans_the_whole_row():
    row = next(row for row in _rows(contents(SECTIONS, 0, 50, 40)) if "Installation" in row)
    assert visible_width(row) == 50


def test_contents_indents_by_heading_level():
    rows = _rows(contents(SECTIONS, 0, 50, 40))
    deep = _plain(next(row for row in rows if "Homebrew" in row))
    shallow = _plain(next(row for row in rows if "Installation" in row))
    assert deep.index("Install with") > shallow.index("Installation")


def test_indenting_is_relative_to_the_shallowest_heading():
    # A document whose headings all start at H2 must not be pushed right.
    rows = _rows(contents([Section("Only", 2, 0, 0)], 0, 50, 40))
    assert _plain(next(row for row in rows if "Only" in row)).index("Only") == 2


def test_contents_reports_a_document_without_headings():
    assert "No headings" in contents([], 0, 50, 40)


def test_contents_scrolls_to_keep_the_selection_visible():
    assert "Section 49" in contents(MANY, 49, 50, 20)
    assert "Section 0" in contents(MANY, 0, 50, 20)
    assert "Section 25" in contents(MANY, 25, 50, 20)


def test_window_centres_without_running_past_either_end():
    assert window(0, 50, 10) == 0
    assert window(25, 50, 10) == 20
    assert window(49, 50, 10) == 40
    assert window(0, 3, 10) == 0  # fewer headings than rows


def test_contents_rows_are_all_the_same_width():
    long_title = [Section("A" * 200, 2, 0, 0)]
    for width in (24, 50, 120):
        for sections in (SECTIONS, long_title, [], MANY):
            rows = [row for row in _rows(contents(sections, 0, width, 40)) if row]
            assert len({visible_width(row) for row in rows}) == 1


def test_contents_never_overflows_a_narrow_terminal_in_display_cells():
    for width in (5, 10):
        output = contents([Section("界界界", 2, 0, 0)], 0, width, 10)
        assert all(visible_width(row) <= width for row in _rows(output))


def test_shortcuts_is_wrapped_in_synchronized_output():
    output = shortcuts(50, 40)
    assert output.startswith("\x1b[?2026h") and output.endswith("\x1b[?2026l")


def test_shortcuts_fills_the_whole_window():
    assert len(_rows(shortcuts(50, 40))) == 40
    assert len(_rows(shortcuts(50, 12))) == 12


def test_shortcuts_lists_every_binding():
    output = shortcuts(80, 40)
    assert "Table of contents" in output
    assert "Next / Previous heading" in output
    assert "Go to beginning / end of document" in output
    assert "Quit" in output
    assert "Keyboard shortcuts" in output
