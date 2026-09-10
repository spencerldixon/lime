# 🍋‍🟩 lime

### Beautiful markdown rendering in your terminal
Built for Ghostty and macOS, might work with other things, idk, I haven't tested it.

![Lime demo](demo.gif "Demo")

## Features

- Large headings
- Formatted tables
- Highlighted code
- Images
- Mermaid diagrams
- Your terminals native theme
- Jump between headings with keyboard shortcuts
- Table of contents navigation
- Keyboard shortcuts help
- Native scrollback
- Native search

## Installation

Requires macOS and [Ghostty](https://ghostty.org) 1.3 or newer. Install with
[uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install git+https://github.com/spencerldixon/lime
```

If you don't have uv:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

This puts a `lime` command on your PATH. uv fetches its own Python and keeps
the dependencies in an isolated environment, so it will not disturb any Python
you already have.

### Allow it to control Ghostty

The first time you run lime, macOS asks whether to allow it to control Ghostty.
**Say yes.** Moving between headings works by asking Ghostty to scroll its own
viewport, over the Apple Events automation link.

If you dismiss that prompt, the document still renders and scrolls by hand, but
`t`, `n`, `p`, `g` and `G` will silently do nothing, which looks like the keys
are broken. 

Grant it later under `System Settings → Privacy & Security → Automation`, then restart lime.

Mermaid diagrams require installing the npm package; see [Mermaid and local images](#mermaid-and-local-images).

### Usage

```sh
lime README.md                                                     # read something
```

### Updating and Removing

```sh
uv tool install --force git+https://github.com/spencerldixon/lime  # update
uv tool uninstall lime-markdown                                    # remove
```

Note: The command is `lime`; the package is `lime-markdown`, which is the name to use
when updating or removing it.


## Usage

Give lime a file to read:

```sh
lime README.md
```

| Key | Action |
| --- | --- |
| `t` | Table of contents |
| `n` / `p` | Next / Previous heading |
| `g` / `G` | Go to beginning / end of document |
| `?` | Keyboard shortcuts |
| `q`, Ctrl-C, Ctrl-D | Quit |

## How lime works

### Big headings are pictures

A terminal draws one size of text, so a large heading is not something you can
just ask for. 

Ghostty supports the [Kitty graphics
protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), which lets a
program hand the terminal a PNG inside an escape sequence and have it drawn
inline. 

For `#`, `##` and `###`, lime renders the heading text into a
transparent image with Pillow and sends it (`src/lime/graphics.py`). The colours
come from asking the terminal what its own palette is, so headings match
whatever theme you use.

The catch is that an image is not text: you cannot search it, select it or copy
it. So underneath each one lime also prints a small dim `## Heading` line. That
line is what ⌘F finds and what you copy. Anything deeper than `###`, and any
heading nested inside a list or quote, stays ordinary text.

### The document lives in the scrollback

Most terminal readers take over the screen using the *alternate screen*, a
second blank buffer the terminal keeps for full-screen programs. It is why
quitting `less` makes everything vanish, and why your terminal's own search
never really applied to what you were reading.

Lime does the opposite. It clears the screen and scrollback once, prints the
document straight into your normal terminal, and stays out of the alternate
screen. The document simply becomes scrollback: your trackpad scrolls it, `⌘F`
searches it, selection copies it, and it is all still there after lime exits.
Resizing the window reprints it once the drag settles, at the new measure, and
puts you back on the heading you were reading.

The one exception is the table of contents panel, which does use the alternate
screen so it can fill the window without shoving your document upwards. Closing
it puts the screen back untouched, and while it is open Ghostty's search applies
to the panel rather than the document.

The document is printed at a fixed measure (`width`, 88 columns by default) and
centred in the window, so a wider window adds even margins on both sides rather
than stretching the text.

### Jumping between heading sections

Ghostty owns the scrollback, so lime asks Ghostty to move its viewport rather
than moving any text itself.

Because lime clears the scrollback before printing, the document starts at row 0
and lime knows the exact row each top-level heading landed on (it counts the
rows as it prints). A jump is then a `scroll_to_row` to that heading's row —
Ghostty puts it at the top of the window, with none of the anchor-then-walk a
prompt-mark jump needs. `g` goes to row 0, `G` scrolls to the foot, and the
contents panel jumps straight to the chosen heading's row. Headings nested
inside lists or quotes get no row of their own and are left out of the contents,
since there is nowhere sensible to jump to.

Ghostty scrolls its own viewport to the bottom a frame or two after any keypress
(its `scroll-to-bottom = keystroke` default), at a moment lime cannot predict,
and that would land after lime's jump and undo it. So lime does not jump once --
it re-issues the same scroll a handful of times over the next ~60ms, spread out
so at least one lands after Ghostty's snap and wins. Each is a sub-millisecond
Apple Event, so the cost is nil. Setting `scroll-to-bottom = no-keystroke` in
your Ghostty config removes the race, but lime does not need you to.

An earlier version used Ghostty's `jump_to_prompt` over OSC 133 prompt marks,
counting bookmarks back from the bottom of the window. That worked until a
resize: reprinting needs to clear the scrollback, and clearing it leaves
Ghostty's prompt-mark bookkeeping stale, so the counts drifted. Absolute rows
have no count to get wrong.

Working out *which* Ghostty window lime is running in turns out to be its own
small problem, covered below.

The window title shows `lime · DOC.md · Configure 2/3` so you can see where you
are even when scrolled deep into history, where a status line printed under the
document would be off screen. It only tracks jumps lime made: it cannot see your
trackpad.

### Keys

Lime reads keys from `/dev/tty`, the controlling terminal, rather than from
stdin. That matters because `cat DOC.md | lime -` already uses stdin for the
document, so keystrokes have to come from somewhere else.

That terminal is put in raw mode, which means keys arrive the instant they are
pressed instead of a line at a time, and Ctrl-C arrives as a plain byte for lime
to handle. `src/lime/keys.py` turns those bytes into events, which is less
trivial than it sounds: an arrow key is a multi-byte escape sequence, and a bare
Escape looks exactly like the start of one until enough time passes.

Deciding what a key means is a pure function. `step(state, key, sections)` in
`src/lime/reader.py` takes the current state and returns the next state plus an
action to perform, like `JUMP` or `OPEN`. The loop around it does the actual
work of talking to the terminal. Keeping the decision separate from the effect
is why most of the behaviour can be tested without a real terminal at all.

### When scrolling is unavailable

Moving Ghostty's viewport goes through Apple Events, spoken in-process through
ScriptingBridge (an earlier version shelled out to `osascript` per jump, which
cost 40-80ms each). To find itself among Ghostty's terminals, lime briefly
renames its window to a random value and asks which terminal carries that name,
then restores the title. Anything else that owns the title, such as a shell hook
or another full-screen program, can overwrite that name first; lime then falls
back to whichever terminal is focused, which is the one it was just launched in.
If automation is unavailable or neither approach identifies a terminal, the
navigation keys do nothing — the document still renders, and your trackpad, ⌘F
and selection all still work.

Interactive mode requires Ghostty without tmux, screen, or Zellij, terminal
output, and a controlling terminal for keys. Redirected output and `NO_COLOR`
always print and exit. Markdown piped **into** lime, such as `cat DOC.md | lime -`,
can still use the reader when output is a terminal; keyboard input comes from the
controlling terminal separately.

## Contributing

```sh
git clone https://github.com/spencerldixon/lime && cd lime
uv sync
uv run pytest
uv run ruff check src tests scripts
uv run lime examples/kitchen-sink.md
```

`uv run lime` runs the checkout without installing it, so it will not clash with
an installed copy. 

We ship a test document at `examples/kitchen-sink.md` that includes
headings, tables, code, local and remote image cases, Mermaid, and clearly
labelled fallbacks for unsupported extensions. 

Most behaviour is covered by `uv run pytest` without needing a real terminal, 
but anything touching Ghostty's viewport should be checked by eye in Ghostty.

## Configuration and spacing

Lime reads `$XDG_CONFIG_HOME/lime/config.yaml`, or `~/.config/lime/config.yaml`,
and uses the built-in defaults if that file is absent. It is the only way to
configure lime: the command takes a document and nothing else. Unknown keys or
wrong types produce an error. Lime never reads configuration from a document's
directory.

There are two settings, both optional:

```yaml
width: 88             # Content measure in columns
vertical_padding: 3   # Blank rows above and below the document
```

The document is rendered at `width` columns and **centred** in the window: a
wider window adds an equal margin on both sides, a window narrower than `width`
drops the margin and wraps to fit. `vertical_padding` adds blank rows above and
below, capped in very short windows. Redirected output omits all of it and wraps
at `width`.

Colours always come from the terminal's theme. Code blocks are numbered (very
narrow blocks omit the numbers to keep room for the source). Big headings render
as images in Ghostty and as text elsewhere.

## Rendering Mermaid diagrams and local images

Install the optional official renderer with `npm install -g @mermaid-js/mermaid-cli`.

This adds Node/Chromium dependencies, so it is deliberately separate from the
small core package. Once `mmdc` is on PATH, top-level fenced `mermaid` blocks render
automatically in Ghostty. Colours are derived from the terminal's foreground,
background, and accent palette. Explicit styling inside a diagram may override
those colours. Rendering has a 30-second timeout; missing dependencies, invalid
syntax, and renderer failures leave the readable source in place.

For images, write `![Caption](path/to/image.png)` on its own paragraph. Paths are
relative to the document. Remote URLs, unsupported formats, missing images, and
images inside lists/quotes retain their caption/link. PNG/JPEG/WebP files are
limited to 20 MB and 20 megapixels.

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

## Reading from a pipe or into a file

```sh
cat DOC.md | lime -
lime DOC.md > rendered.txt
```

Lime takes one argument: a document, or `-` for stdin. Image headings need
Ghostty and a successful palette query; they are disabled inside tmux, screen,
and Zellij, and under redirected output or `NO_COLOR`. Elsewhere headings fall
back to text.

## Limits and design choices

Ghostty 1.3+ is required for its built-in search UI. The large headings use the
[Kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/), like
the original proof of concept. They are images, not differently sized terminal
characters. Ghostty's [OSC 66 text-sizing issue](https://github.com/ghostty-org/ghostty/issues/10333)
is still open as checked on 9 September 2026. The small heading labels are what
make those titles searchable and copyable today.

Lime clears the scrollback when it starts and again on every resize reprint, so
whatever was in the terminal above it is gone. Ghostty's configured scrollback
and image storage limits apply, so exceptionally long documents can lose older
content. The heading font uses Helvetica Neue on macOS, common system fonts on
Linux, or Pillow's fallback, and is not configurable in this release.

HTML layout, LaTeX, remote image downloads, live refresh, and section paging are
outside v0.1. Raw HTML is displayed literally. Terminal control characters in
document text are removed. Code is only displayed; Mermaid source is passed to
the optional local renderer. Lime itself does not fetch remote document content.
