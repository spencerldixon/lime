from lime.outline import Section, filtered
from lime.overlay import MINIMUM, erase, height, help_text, picker, reserve

SECTIONS = [Section("Installation", 2, 0, 0), Section("Install with Homebrew", 3, 1, 4)]


def test_height_is_capped_and_floored():
    assert height(40) == 12
    assert height(14) == 10
    assert height(10) == 6
    assert height(9) == 0


def test_minimum_is_six():
    assert MINIMUM == 6


def test_reserve_scrolls_then_returns_to_the_top_of_the_block():
    assert reserve(6) == "\n" * 6 + "\x1b[6A"


def test_erase_clears_to_end_of_screen():
    assert erase() == "\r\x1b[J"


def test_picker_is_wrapped_in_synchronized_output():
    output = picker(filtered(SECTIONS, ""), 0, "", 50, 40)
    assert output.startswith("\x1b[?2026h") and output.endswith("\x1b[?2026l")


def test_picker_shows_titles_levels_and_query():
    output = picker(filtered(SECTIONS, "ins"), 0, "ins", 50, 40)
    assert "ins" in output and "Installation" in output and "H2" in output and "H3" in output


def test_picker_marks_the_selected_entry():
    first = picker(filtered(SECTIONS, ""), 0, "", 50, 40)
    second = picker(filtered(SECTIONS, ""), 1, "", 50, 40)
    assert first.index("›") < first.index("Installation")
    assert second.index("›") > second.index("Installation")


def test_picker_indents_by_heading_level():
    output = picker(filtered(SECTIONS, ""), 0, "", 50, 40)
    lines = output.splitlines()
    deep = next(line for line in lines if "Homebrew" in line)
    shallow = next(line for line in lines if "Installation" in line)
    assert deep.index("Install with") > shallow.index("Installation")


def test_picker_reports_an_empty_result():
    assert "No matching" in picker([], 0, "zzz", 50, 40)


def test_picker_reports_a_document_without_headings():
    assert "No headings" in picker([], 0, "", 50, 40)


def test_picker_returns_nothing_when_the_window_is_too_short():
    assert picker(filtered(SECTIONS, ""), 0, "", 50, 9) == ""


def test_picker_never_exceeds_its_height():
    # Not splitlines(): the frame contains bare \r characters (cursor moves) in
    # addition to the \r\n row separators, and splitlines() breaks on both, so
    # it overcounts rows. split("\r\n") isolates the actual rendered rows.
    many = [Section(f"Section {n}", 2, n, n) for n in range(50)]
    output = picker(filtered(many, ""), 0, "", 50, 40)
    assert len(output.split("\r\n")) <= height(40)


def test_picker_scrolls_to_keep_the_selection_visible():
    many = [Section(f"Section {n}", 2, n, n) for n in range(50)]
    assert "Section 49" in picker(filtered(many, ""), 49, "", 50, 40)


def test_help_lists_lime_and_ghostty_keys():
    output = help_text(60, 40, None)
    for key in ("t", "?", "q", "n", "p", "g", "G"):
        assert key in output
    assert "⌘F" in output


def test_help_explains_a_disabled_jump():
    assert "reprint" in help_text(60, 40, "reprint")


def _rows(output: str) -> list[str]:
    """The rendered rows of a frame, stripped of the sync wrapper and cursor moves."""
    rows = output.split("\r\n")
    rows[0] = rows[0].removeprefix("\x1b[?2026h\r")
    rows[-1] = rows[-1].rsplit("\r\x1b[", 1)[0]
    return [row.removesuffix("\x1b[K") for row in rows]


def test_picker_rows_are_all_the_same_width():
    # Every row must line up so the box borders form a straight edge, whether the
    # content is short (padded) or long (truncated) and at any overlay width.
    long_title = [Section("A" * 200, 2, 0, 0)]
    for width in (24, 50, 120):
        for entries, query in (
            (filtered(SECTIONS, ""), ""),
            (filtered(SECTIONS, "ins"), "ins"),
            (filtered(long_title, ""), ""),
            ([], "z" * 100),
            ([], ""),
        ):
            rows = [row for row in _rows(picker(entries, 0, query, width, 40)) if row]
            assert len({len(row) for row in rows}) == 1


def test_help_rows_are_all_the_same_width():
    for width in (24, 60, 120):
        for jump in (None, "reprint", "x" * 200):
            rows = [row for row in _rows(help_text(width, 40, jump)) if row]
            assert len({len(row) for row in rows}) == 1
