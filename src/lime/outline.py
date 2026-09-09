"""Document headings as a navigable model, independent of any terminal."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_it.token import Token


@dataclass(frozen=True)
class Section:
    title: str
    level: int
    index: int
    line: int


def heading_text(token: Token) -> str:
    """Flatten inline content of a heading, extracting plain text."""
    return "".join(
        heading_text(child)
        if child.children
        else " "
        if child.type in {"softbreak", "hardbreak"}
        else child.content
        for child in token.children or []
    )


def outline(tokens: list[Token]) -> list[Section]:
    """Top-level headings only: exactly those that receive a prompt mark."""
    sections = []
    for position, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0:
            sections.append(
                Section(
                    heading_text(tokens[position + 1]),
                    int(token.tag[1]),
                    len(sections),
                    token.map[0] if token.map else 0,
                )
            )
    return sections


def match(query: str, title: str) -> tuple[int, ...] | None:
    """Character positions of a case-insensitive subsequence, or None."""
    positions = []
    haystack = title.casefold()
    start = 0
    for character in query.casefold():
        found = haystack.find(character, start)
        if found < 0:
            return None
        positions.append(found)
        start = found + 1
    return tuple(positions)


def filtered(
    sections: list[Section], query: str
) -> list[tuple[Section, tuple[int, ...]]]:
    """Sections matching a subsequence query, with match positions per section."""
    results = []
    for section in sections:
        positions = match(query, section.title)
        if positions is not None:
            results.append((section, positions))
    return results
