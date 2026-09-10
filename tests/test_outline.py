from markdown_it import MarkdownIt

from lime.outline import Section, outline, search


def parse(source):
    return MarkdownIt("commonmark", {"html": False}).enable(["strikethrough", "table"]).parse(source)


def test_outline_captures_level_title_and_order():
    sections = outline(parse("# One\n\ntext\n\n## Two\n\n### Three\n"))
    assert sections == [
        Section("One", 1, 0, 0),
        Section("Two", 2, 1, 4),
        Section("Three", 3, 2, 6),
    ]


def test_duplicate_titles_stay_distinct_by_line():
    sections = outline(parse("## Install\n\nfirst\n\n## Install\n\nsecond\n"))
    assert [section.line for section in sections] == [0, 4]
    assert [section.index for section in sections] == [0, 1]


def test_inline_formatting_is_flattened():
    assert outline(parse("# A `code` and **bold**\n"))[0].title == "A code and bold"


def test_headings_inside_lists_and_quotes_are_skipped():
    # These render as plain text and receive no prompt mark, so the outline
    # must not list them or index and mark order would drift apart.
    assert outline(parse("- # Nested\n\n> ## Quoted\n")) == []


def test_document_without_headings():
    assert outline(parse("Just a paragraph.\n")) == []


HEADINGS = [
    Section("Installation", 2, 0, 0),
    Section("Install with uv", 3, 1, 4),
    Section("Configuration", 2, 2, 8),
]


def test_search_matches_titles_case_insensitively_in_order():
    assert search(HEADINGS, "install") == [HEADINGS[0], HEADINGS[1]]
    assert search(HEADINGS, "ATION") == [HEADINGS[0], HEADINGS[2]]


def test_search_matches_anywhere_in_the_title():
    assert search(HEADINGS, "with uv") == [HEADINGS[1]]


def test_a_blank_query_returns_every_section():
    assert search(HEADINGS, "") == HEADINGS
    assert search(HEADINGS, "   ") == HEADINGS


def test_search_returns_nothing_when_no_title_matches():
    assert search(HEADINGS, "zzz") == []
