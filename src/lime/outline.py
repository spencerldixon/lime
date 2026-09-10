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


def search(sections: list[Section], query: str) -> list[Section]:
    """Sections whose title contains query, case-insensitively, in outline order.

    An empty or blank query matches everything, so the outline is unfiltered
    until the reader actually types something.
    """
    if not query.strip():
        return list(sections)
    needle = query.casefold()
    return [section for section in sections if needle in section.title.casefold()]


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
