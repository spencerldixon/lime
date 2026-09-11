import base64
import io
import re
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from rich.cells import cell_len
from rich.console import Console

from lime.cache import ImageCache
from lime.graphics import Headings, encode_png, load_font, wrap_heading
from lime.outline import Section
from lime.render import RowCounter, markdown_theme, render
from lime.terminal import Terminal

PALETTE = ((100, 180, 80), (80, 180, 180), (100, 140, 220))


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


def _anchored(source, width=40):
    counter = RowCounter(io.StringIO())
    console = Console(file=counter, width=width, force_terminal=True, color_system="truecolor")
    anchors: list[int] = []
    sections = render(source, console, base=Path.cwd(), anchors=anchors)
    return sections, anchors, counter._inner.getvalue()


def test_anchors_are_the_document_start_then_one_per_top_level_heading():
    sections, anchors, _ = _anchored("# One\n\ntext\n\n## Two\n")
    assert len(anchors) == len(sections) + 1  # a leading document-start anchor
    assert anchors == sorted(anchors)  # in reading order


def test_a_nested_heading_gets_no_anchor_and_no_outline_entry():
    sections, anchors, _ = _anchored("- # Nested\n")
    assert sections == [] and anchors == [0]  # only the document-start anchor


def test_a_headingless_document_still_has_the_start_anchor():
    sections, anchors, _ = _anchored("Just a paragraph, no headings.\n")
    assert sections == [] and anchors == [0]


def test_anchors_add_no_escapes_and_leave_plain_output_clean():
    stream = io.StringIO()
    render("# One\n\nbody\n", Console(file=stream, width=40), base=Path.cwd(), anchors=[])
    assert "\x1b" not in stream.getvalue()  # nothing is written for an anchor


@pytest.mark.parametrize(
    "change",
    ["text", "level", "width", "cell_width", "cell_height", "palette", "font"],
)
def test_heading_cache_accounts_for_every_raster_input(monkeypatch, change):
    calls = []

    def font(size, custom=None):
        calls.append((size, custom))
        return load_font(size)

    monkeypatch.setattr("lime.graphics.load_font", font)
    cache = ImageCache()
    terminal = Terminal()
    original = list(Headings(terminal, 40, PALETTE, cache=cache).images("Title", 1))
    assert list(Headings(terminal, 40, PALETTE, cache=cache).images("Title", 1)) == original
    assert len(calls) == 1

    text, level, width, palette, custom = "Title", 1, 40, PALETTE, None
    if change == "text":
        text = "Another title"
    elif change == "level":
        level = 2
    elif change == "width":
        width = 20
    elif change in {"cell_width", "cell_height"}:
        terminal = replace(terminal, **{change: getattr(terminal, change) + 1})
    elif change == "palette":
        palette = tuple(reversed(PALETTE))
    else:
        custom = Path("another-font.ttf")
    list(Headings(terminal, width, palette, custom, cache=cache).images(text, level))
    assert len(calls) == 2


def test_warm_heading_cache_preserves_output_and_anchor_rows():
    source = "# A heading long enough to wrap\n\nBody.\n\n## Café 日本語\n"
    headings = Headings(Terminal(), 20, PALETTE)

    def draw():
        output = io.StringIO()
        console = Console(file=RowCounter(output), width=20, theme=markdown_theme())
        anchors = []
        sections = render(source, console, base=Path.cwd(), headings=headings, anchors=anchors)
        return output.getvalue(), anchors, sections

    cold = draw()
    assert draw() == cold
    assert "# A heading" in cold[0] and "## Café 日本語" in cold[0]


def test_oversized_headings_still_stream_without_being_cached(monkeypatch):
    calls = []

    def font(size, custom=None):
        calls.append(size)
        return load_font(size)

    monkeypatch.setattr("lime.graphics.load_font", font)
    headings = Headings(Terminal(), 20, PALETTE, cache=ImageCache(max_bytes=1))
    first = list(headings.images("A long heading that wraps", 1))
    assert len(first) > 1
    assert list(headings.images("A long heading that wraps", 1)) == first
    assert len(calls) == 2


def test_failed_heading_render_keeps_text_and_can_retry(monkeypatch):
    calls = []

    def font(size, custom=None):
        calls.append(size)
        if len(calls) == 1:
            raise OSError("font unavailable")
        return load_font(size)

    monkeypatch.setattr("lime.graphics.load_font", font)
    headings = Headings(Terminal(), 40, PALETTE)
    outputs = []
    for _ in range(3):
        stream = io.StringIO()
        render("# Title", Console(file=stream, width=40), base=Path.cwd(), headings=headings)
        outputs.append(stream.getvalue())
    assert "Title" in outputs[0] and "\x1b_G" not in outputs[0]
    assert "# Title" in outputs[1] and "\x1b_G" in outputs[1]
    assert outputs[1] == outputs[2]
    assert len(calls) == 2
