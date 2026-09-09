import base64
import io
import re
from pathlib import Path

import pytest
from PIL import Image
from rich.cells import cell_len
from rich.console import Console

from lime.graphics import Headings, encode_png, load_font, wrap_heading
from lime.outline import Section
from lime.render import markdown_theme, render
from lime.terminal import Terminal

PALETTE = ((100, 180, 80), (80, 180, 180), (100, 140, 220))
MARK = "\x1b]133;A\x1b\\"


def output(source, width=80, graphics=False, labels=True, color=False):
    stream = io.StringIO()
    console = Console(
        file=stream,
        width=width,
        force_terminal=color,
        color_system="truecolor" if color else None,
        theme=markdown_theme(),
    )
    render(
        source,
        console,
        base=Path("/tmp/docs"),
        headings=Headings(Terminal(), width, PALETTE) if graphics else None,
        heading_labels=labels,
    )
    return stream.getvalue()


def test_markdown_and_references_survive_heading_boundaries():
    result = output(
        "# **First**\n\nA [reference][doc].\n\n## Second\n\n"
        "- Parent\n  - Child\n\n> A quote\n\n[doc]: https://ghostty.org\n",
        graphics=True,
    )
    assert "# First" in result
    assert "## Second" in result
    assert "reference (https://ghostty.org)" in result
    assert all(word in result for word in ("Parent", "Child", "A quote"))


@pytest.mark.parametrize("width", [12, 20, 30, 40, 80, 120])
def test_tables_preserve_every_column_and_fit(width):
    source = (
        "| Name | State | Score | Extra |\n|:--|:--:|--:|--|\n"
        "| apple | ready | 42 | final |\n| pear | waiting | 123 | done |\n"
    )
    result = output(source, width)
    assert all(word in result for word in ("apple", "ready", "42", "final", "123", "done"))
    assert all(cell_len(line) <= width for line in result.splitlines())


def test_code_preserves_blank_lines_and_does_not_parse_headings():
    result = output("```python\n# not a heading\nx = 1\n\n\n```\n", graphics=True)
    assert "\033_G" not in result
    assert "# not a heading" in result
    assert "x = 1" in result
    assert "python" in result


def test_long_code_wraps_without_losing_characters():
    result = output("```unknown_language\n" + "x" * 120 + "\n```", width=30)
    assert result.count("x") == 120
    assert all(cell_len(line) <= 30 for line in result.splitlines())


def test_nested_headings_stay_in_their_container():
    result = output("> ## Inside a quote\n> text\n\n- ### Inside a list\n", graphics=True)
    assert "\033_G" not in result
    assert "Inside a quote" in result and "Inside a list" in result


def test_decoded_entities_and_raw_controls_cannot_inject_commands():
    result = output(
        "# Bad &#27;]52;c;payload&#7;\n\n"
        "text\x1b]52;c;raw\x07\n\n[link](https://example.com/&#27;)\n",
        color=False,
    )
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", result)


def test_links_resolve_relative_to_document_and_emit_osc8():
    result = output("[local](../other.md) and [web](https://ghostty.org)", color=True)
    assert "\033]8;" in result
    assert "file:///private/tmp/other.md" in result or "file:///tmp/other.md" in result
    assert "https://ghostty.org" in result


def test_html_is_visible_and_not_executed():
    result = output("<details>\n<summary>Hidden?</summary>\n\nUseful content\n</details>")
    assert "Hidden?" in result and "Useful content" in result


def test_image_only_headings_are_opt_in():
    result = output("# Unique title", graphics=True, labels=False)
    assert "\033_G" in result and "Unique title" not in result


def test_image_packets_are_bounded_quiet_and_decode_to_png():
    headings = Headings(Terminal(cell_width=9, cell_height=20), 40, PALETTE)
    for png, columns, rows in headings.images("A long heading that wraps across lines", 1):
        packets = list(encode_png(png, columns, rows))
        assert "q=2" in packets[0] and "C=1" in packets[0]
        assert "m=0;" in packets[-1]
        payloads = [packet.split(";", 1)[1][:-2] for packet in packets]
        assert all(len(payload) <= 4096 for payload in payloads)
        image = Image.open(io.BytesIO(base64.b64decode("".join(payloads))))
        assert image.size == (columns * 9 * 2, rows * 20 * 2)
        assert columns <= 40 and rows == 3
        assert image.getbbox() is not None


def test_heading_wrapping_preserves_long_words():
    font = load_font(30)
    words = "A verylongunbrokentitlethatmustwrap safely"
    lines = list(wrap_heading(words, font, 120))
    assert "".join(lines).replace(" ", "") == words.replace(" ", "")
    assert all(font.getlength(line) <= 120 for line in lines)


def test_setext_heading_gets_correct_search_label():
    assert "# Title" in output("Title\n=====", graphics=True)


@pytest.mark.parametrize("width", [20, 40, 80])
def test_unicode_tables_fit_terminal_cells(width):
    result = output("| 名称 | 値 |\n|---|---|\n| 日本語 | café 🍋 |", width=width)
    assert "日本語" in result and "café" in result
    assert all(cell_len(line) <= width for line in result.splitlines())


def test_render_returns_the_outline():
    console = Console(file=io.StringIO(), width=40)
    sections = render("# One\n\n## Two\n", console, base=Path.cwd())
    assert sections == [Section("One", 1, 0, 0), Section("Two", 2, 1, 2)]


def test_marks_precede_every_top_level_heading():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    render("# One\n\ntext\n\n## Two\n", console, base=Path.cwd(), marks=True)
    output_text = stream.getvalue()
    # One document-start mark plus one per top-level heading (mark 0 = start).
    assert output_text.count(MARK) == 3
    assert output_text.index(MARK) < output_text.index("One")
    assert output_text.index(MARK, output_text.index("One")) < output_text.index("Two")


def test_no_marks_unless_requested():
    stream = io.StringIO()
    console = Console(file=stream, width=40)
    render("# One\n", console, base=Path.cwd())
    assert MARK not in stream.getvalue()


def test_nested_headings_get_no_marks_and_no_outline_entry():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    sections = render("- # Nested\n", console, base=Path.cwd(), marks=True)
    output_text = stream.getvalue()
    # Only the document-start mark: the nested heading gets no mark of its own.
    assert sections == [] and output_text.count(MARK) == 1


def test_headingless_document_still_gets_a_start_mark():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    sections = render("Just a paragraph, no headings.\n", console, base=Path.cwd(), marks=True)
    output_text = stream.getvalue()
    assert sections == []
    assert output_text.count(MARK) == 1
    assert output_text.index(MARK) < output_text.index("Just a paragraph")


def test_mark_count_is_sections_plus_one_for_the_document_start():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    render("# One\n\n## Two\n", console, base=Path.cwd(), marks=True)
    assert stream.getvalue().count(MARK) == 3


def test_start_mark_precedes_all_content_including_the_first_heading_mark():
    stream = io.StringIO()
    console = Console(file=stream, width=40, force_terminal=True, color_system="truecolor")
    render("# One\n", console, base=Path.cwd(), marks=True)
    output_text = stream.getvalue()
    first_mark_end = output_text.index(MARK) + len(MARK)
    assert output_text.index(MARK, first_mark_end) < output_text.index("One")


def test_no_start_mark_unless_marks_requested():
    stream = io.StringIO()
    console = Console(file=stream, width=40)
    render("No headings here.\n", console, base=Path.cwd())
    assert MARK not in stream.getvalue()
