"""CommonMark + tables/strikethrough, with a small set of terminal layouts."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token
from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown, TableElement
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from lime.graphics import Headings
from lime.outline import Section, heading_text, outline
from lime.terminal import clean_text


class TextHeading(Heading):
    LEVEL_ALIGN: ClassVar = {f"h{level}": "left" for level in range(1, 7)}


class FencedCode(CodeBlock):
    @classmethod
    def create(cls, markdown, token):
        block = super().create(markdown, token)
        block.line_numbers = markdown.line_numbers
        return block

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # Remove only the parser's final newline, preserving trailing spaces and
        # blank lines in examples. Unknown language labels fall back via Rich.
        syntax = Syntax(
            self.text.plain.removesuffix("\n"),
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            background_color="default",
            line_numbers=self.line_numbers and options.max_width >= 20,
        )
        if options.max_width < 12:
            yield syntax
        else:
            yield Panel(
                syntax,
                title=Text(self.lexer_name, style="dim"),
                title_align="left",
                box=box.ROUNDED,
                border_style="dim",
                padding=(0, 1),
            )


class ResponsiveTable(TableElement):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        headers = self.header.row.cells if self.header and self.header.row else []
        rows = self.body.rows if self.body else []
        # Rich can hide entire columns when there are too many to fit. Switch to
        # labelled records before that happens so every value remains readable.
        if len(headers) * 10 + 1 > options.max_width:
            if not rows:
                for header in headers:
                    yield header.content
            for index, row in enumerate(rows):
                if index:
                    yield Text()
                for header, cell in zip(headers, row.cells):
                    label = header.content.copy()
                    label.stylize("bold")
                    yield Text.assemble(label, ": ", cell.content)
            return
        table = Table(box=box.ROUNDED, border_style="dim", padding=(0, 1), expand=False)
        for header in headers:
            table.add_column(header.content, header_style="bold", overflow="fold")
        for row in rows:
            table.add_row(*(cell.content for cell in row.cells))
        yield table


class Document(Markdown):
    elements: ClassVar = {
        **Markdown.elements,
        "heading_open": TextHeading,
        "fence": FencedCode,
        "code_block": FencedCode,
        "table_open": ResponsiveTable,
    }

    def __init__(self, markup: str, *, line_numbers: bool = True, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.line_numbers = line_numbers
        # HTML is displayed literally; it must not silently swallow document text.
        self.parsed = (
            MarkdownIt("commonmark", {"html": False})
            .enable(["strikethrough", "table"])
            .parse(markup)
        )


def safe_link(href: str, base: Path) -> str:
    """Resolve local links against the document, preserving URL fragments."""
    href = clean_text(href)
    try:
        parts = urlsplit(href)
    except ValueError:
        return ""
    if parts.scheme:
        return href if parts.scheme.lower() in {"https", "http", "mailto", "file"} else ""
    if parts.netloc:
        return "https:" + href
    if not parts.path:
        return href
    result = (base / unquote(parts.path)).resolve().as_uri()
    if parts.query:
        result += "?" + parts.query
    if parts.fragment:
        result += "#" + parts.fragment
    return result


def prepare(tokens: list[Token], base: Path) -> None:
    for token in tokens:
        token.content = clean_text(token.content)
        token.info = clean_text(token.info)
        for name in ("href", "src"):
            value = token.attrGet(name)
            if value:
                token.attrSet(name, safe_link(value, base))
        if token.children:
            prepare(token.children, base)


def markdown_theme() -> Theme:
    return Theme(
        {
            "markdown.h1": "bold green",
            "markdown.h2": "bold cyan",
            "markdown.h3": "bold blue",
            "markdown.h4": "bold",
            "markdown.h5": "bold",
            "markdown.h6": "dim bold",
            "markdown.code": "cyan",
            "markdown.link": "underline cyan",
            "markdown.link_url": "underline cyan",
            "markdown.block_quote": "italic",
        }
    )


def render(
    source: str,
    console: Console,
    *,
    base: Path,
    margin: int = 0,
    headings: Headings | None = None,
    heading_labels: bool = True,
    line_numbers: bool = True,
    marks: bool = False,
    mermaid=None,
    images=None,
) -> list[Section]:
    document = Document(
        clean_text(source),
        code_theme="ansi_light",
        hyperlinks=console.is_terminal and console.color_system is not None,
        line_numbers=line_numbers,
    )
    prepare(document.parsed, base)
    tokens = document.parsed
    sections = outline(tokens)

    def emit(batch: list[Token]) -> None:
        if batch:
            document.parsed = batch
            console.print(Padding(document, (0, margin)), highlight=False)

    batch: list[Token] = []
    index = 0
    if marks:
        # Mark 0 is the document start, so lime can always open at the first
        # character even when there are no headings at all. Mark i + 1 is the
        # heading at outline index i, so total marks == len(sections) + 1.
        console.file.write("\x1b]133;A\x1b\\")
    while index < len(tokens):
        token = tokens[index]
        if marks and token.type == "heading_open" and token.level == 0:
            # Ghostty records a prompt mark here, so its own jump_to_prompt
            # walks headings. The predicate must match outline() exactly.
            emit(batch)
            batch = []
            console.file.write("\x1b]133;A\x1b\\")
        if (
            images
            and token.type == "paragraph_open"
            and token.level == 0
            and index + 2 < len(tokens)
            and tokens[index + 1].type == "inline"
            and len(tokens[index + 1].children or []) == 1
            and tokens[index + 1].children[0].type == "image"
        ):
            emit(batch)
            batch = []
            picture = tokens[index + 1].children[0]
            images.write(console.file, picture.attrGet("src") or "", margin)
            emit(tokens[index : index + 3])
            index += 3
        elif (
            mermaid
            and token.type == "fence"
            and token.level == 0
            and token.info.strip() == "mermaid"
        ):
            emit(batch)
            batch = []
            try:
                mermaid.write(console.file, token.content, margin)
            except (OSError, ValueError) as error:
                console.print(Padding(Text(f"Mermaid: {error}", style="dim"), (0, margin)))
            # Keep the source as real terminal text, including all diagram labels.
            emit([token])
            index += 1
        elif (
            headings
            and token.type == "heading_open"
            and token.level == 0
            and token.tag in {"h1", "h2", "h3"}
        ):
            emit(batch)
            batch = []
            title = heading_text(tokens[index + 1])
            console.print()
            try:
                headings.write(console.file, title, int(token.tag[1]), margin)
            except (OSError, ValueError):
                # Missing fonts / pathological sizes still leave a readable title.
                emit(tokens[index : index + 3])
            else:
                if heading_labels:
                    label = Text(f"{'#' * int(token.tag[1])} {title}", style="dim")
                    console.print(Padding(label, (0, margin)))
            console.print()
            index += 3
        else:
            batch.append(token)
            index += 1
    emit(batch)
    return sections
