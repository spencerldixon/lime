# 🍋‍🟩 lime

Markdown in the terminal.

Large headings, readable tables, highlighted code, images,
mermaid diagrams, all using your terminal's own 
theme with native scrollback, selection, and search.

Built for ghostty, might work with other things, idk, I haven't tested it.

```sh
lime DOC.md
```

For a comprehensive visual check, run `lime examples/kitchen-sink.md`. It includes
headings, tables, code, local and remote image cases, Mermaid, and clearly labelled
fallbacks for unsupported extensions.

Lime prints into the normal terminal screen and returns to your shell. The whole
document remains in Ghostty's scrollback. Use your trackpad or scrollbar to read
it, and **⌘F** on macOS or **Ctrl+Shift+F** on Linux to search. It doesn't launch a
pager or capture your mouse and navigation keys. Long documents finish at the
bottom; scroll upwards to begin reading.

## Install locally with Homebrew

Requires Homebrew and [uv](https://docs.astral.sh/uv/getting-started/installation/)
to build this checkout. The installed command uses Homebrew's Python and Pillow;
it does not need uv or this checkout at runtime.

```sh
uv sync --locked
uv run python scripts/build_homebrew.py
python3 scripts/install_homebrew.py
lime examples/demo.md
```

The installer creates a local `lime/local` tap (required by current Homebrew),
installs `lime/local/lime`, runs its Homebrew test, and creates your YAML config if
one doesn't exist. Existing configuration is preserved.

The generated formula points to the local release archive in `dist/`. Rebuild the
archive and rerun the installer after changing the source. The formula carries
exact checksums for the archive and its pure Python
dependencies. Pillow comes from Homebrew; its version follows that installation.

For development without installing globally:

```sh
uv run lime examples/demo.md
uv run pytest
uv run ruff check src tests scripts
```

If Homebrew rejects the build because Xcode/Command Line Tools are too old, update
the toolchain through macOS Software Update and rerun the installer. The Python
wheel can also be installed independently with
`uv tool install ./dist/lime_markdown-0.1.0-py3-none-any.whl`.

## What v0.1 renders

- H1–H3 use enlarged, transparent images in Ghostty, with normal text labels for
  native search and copying. H4–H6 and headings inside lists/quotes stay text.
- Headings wrap at the available width using actual glyph measurements.
- Paragraphs, emphasis, lists, blockquotes, rules, inline code, and strikethrough.
- Tables with aligned columns and wrapped cells. At narrow widths, tables become
  labelled records so columns are never silently dropped.
- Fenced and indented code, with line numbers, syntax colours, and wrapped long lines. Unknown
  language names render as plain code. Copying wrapped code may require removing
  display wrapping and borders; use the source for byte-exact copying.
- Clickable web and file links via OSC 8. Relative file links resolve against the
  Markdown file's directory. Plain output includes link destinations.
- UTF-8 files, stdin, and escape-free output when redirected.
- Local PNG/JPEG/WebP images on their own Markdown paragraph, fitted to the
  reading width with searchable alt-text captions. Tall images scroll in slices.
- Mermaid diagrams via an optional local `mmdc` installation. The source remains
  visible and searchable beneath the diagram.

## Configuration and spacing

Lime reads `$XDG_CONFIG_HOME/lime/config.yaml`, or `~/.config/lime/config.yaml`.
It uses the built-in defaults if that file is absent. An explicit `--config FILE`
selects another file; command flags override YAML. Invalid keys/types produce an
error. It never reads configuration from a document's directory automatically.

```yaml
width: 88
padding: 12
vertical_padding: 6
line_numbers: true
headings: auto
heading_labels: true
images: true
mermaid: auto
font: null
```

Padding is on **all four sides**: 12 columns left/right and 6 rows top/bottom,
roughly an inch at typical font sizes. Terminals do not report reliable physical
inches. Horizontal padding shrinks in narrow splits; vertical padding is capped
in very short windows. `width` caps content width, with horizontal padding added
when room permits. Redirected output omits the outer padding.

The [example configuration](config.example.yaml) documents every setting. Code
line numbers are on by default; `--no-line-numbers` disables them. Very narrow
code blocks omit numbers to preserve room for the source.

## Mermaid and local images

Install the optional official renderer with `npm install -g @mermaid-js/mermaid-cli`.
This adds Node/Chromium dependencies, so it is deliberately separate from the
small core package. Once `mmdc` is on PATH, top-level fenced `mermaid` blocks render
automatically in Ghostty. Colours are derived from the terminal's foreground,
background, and accent palette. Explicit styling inside a diagram may override
those colours. Rendering has a 30-second timeout; missing dependencies, invalid
syntax, and renderer failures leave the readable source in place. Disable it with
`--mermaid off` or `mermaid: off` in YAML.

For images, write `![Caption](path/to/image.png)` on its own paragraph. Paths are
relative to the document. Remote URLs, unsupported formats, missing images, and
images inside lists/quotes retain their caption/link. PNG/JPEG/WebP files are
limited to 20 MB and 20 megapixels. `--no-images` disables inline images.

## Your terminal's theme

Body text and backgrounds inherit Ghostty's defaults. Syntax highlighting and
other accents use its ANSI palette. Image headings query that same palette with
OSC 4: green for H1, cyan for H2, and blue for H3. OSC 10/11 provide the foreground
and background for Mermaid. Lime never sets terminal colours
and has no separate theme to configure.

The query briefly reads the controlling terminal, so piping Markdown on stdin
still works. It has a timeout and restores terminal input settings. If the
palette cannot be read, headings fall back to normal themed text. Image colours
are captured at render time: rerun lime after changing Ghostty's theme or font
size. Existing raster images do not recolour themselves.

## Options

```sh
lime --width 72 DOC.md                 # Cap content width (default: 88)
lime --headings text DOC.md            # Every heading is ordinary terminal text
lime --no-heading-labels DOC.md        # Images alone; heading titles aren't searchable
lime --plain DOC.md                    # No colours, images, or escape sequences
lime --font /path/to/font.ttf DOC.md   # Custom enlarged-heading typeface
lime --config config.example.yaml DOC.md
lime --no-line-numbers --no-images --mermaid off DOC.md
cat DOC.md | lime -
lime DOC.md > rendered.txt
```

`--headings image` explicitly enables graphics attempts in a TTY when automatic
Ghostty detection is unavailable, such as some SSH sessions. It still requires
a successful palette query. Redirected output and `NO_COLOR` always disable
images and styling. Automatic graphics are disabled inside tmux, screen, and
Zellij; use lime directly in Ghostty for image headings.

## Limits and design choices

Ghostty 1.3+ is required for its built-in search UI. The large headings use the
[Kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), like
the original proof of concept. They are images, not differently sized terminal
characters. Ghostty's [OSC 66 text-sizing issue](https://github.com/ghostty-org/ghostty/issues/10333)
is still open as checked on 9 September 2026. The small heading labels are what
make those titles searchable and copyable today.

Layout is calculated when the command runs. Resize a split and rerun the command
to reflow tables and image headings. Ghostty's configured scrollback and image
storage limits apply, so exceptionally long documents can lose older content.
The heading font uses Helvetica Neue on macOS, common system fonts on Linux, or
Pillow's fallback. Use `--font` for scripts that need additional glyph coverage.
This release does not automatically discover Ghostty's configured font.

HTML layout, LaTeX, remote image downloads, live refresh, and section paging are
outside v0.1. Raw HTML is displayed literally. Terminal control characters in
document text are removed. Code is only displayed; Mermaid source is passed to
the optional local renderer. Lime itself does not fetch remote document content.

## Publish a Homebrew tap

There is no public tap or uploaded release yet. Build the concrete release first:

```sh
uv run python scripts/build_homebrew.py \
  --url https://YOUR_RELEASE_HOST/lime-0.1.0.tar.gz \
  --homepage https://YOUR_PROJECT_HOMEPAGE
```

Upload the generated `dist/lime-0.1.0.tar.gz` without modifying it, and place
`Formula/lime.rb` in a `homebrew-lime` Git repository. Users can then install with
`brew install OWNER/lime/lime`, where `OWNER` is the tap's actual GitHub owner.
The uppercase values above are placeholders to replace when choosing where to
publish. Nothing in the build script publishes or changes an external repository.

See [the next-version discussion](docs/ideas.md) for section paging and other ideas.
