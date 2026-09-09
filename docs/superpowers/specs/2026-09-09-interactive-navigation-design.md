# Interactive navigation

Status: design, approved for planning. Date: 9 September 2026.

A table of contents, section jumping, a persistent control bar, and a keyboard
help modal — without giving up Ghostty's native scrollback, selection, or search.

## The governing constraint

Lime v0.1 prints a document into the normal terminal screen and returns to the
shell. Ghostty owns scrolling, selection, and search; `terminal.py` is titled
"Output-only terminal integration: leave input, scrollback and search to
Ghostty." That is the product, not an implementation detail. Any design that
enters the alternate screen buffer to gain a viewport gives up native scrollback
and ⌘F, which is the reason to use lime instead of a pager.

So lime does not acquire a viewport. It annotates the document with marks the
terminal already understands, stays alive to accept keys, and asks Ghostty to
move its own viewport. Ghostty keeps owning the buffer throughout.

`docs/ideas.md` states that "terminal escape sequences do not offer a portable
arbitrary-row jump into Ghostty's native scrollback" and treats the jump as the
open architectural question. That conclusion is outdated for Ghostty 1.3.1.
`ghostty +list-actions` exposes `scroll_to_row`, `scroll_to_top`,
`scroll_to_bottom`, `scroll_to_selection`, `scroll_page_lines`, and
`jump_to_prompt`. The AppleScript dictionary exposes `perform action` taking an
arbitrary action string against a target terminal surface and returning a
boolean. The jump is available. `docs/ideas.md` is superseded by this document
and must be updated when this ships.

## Design principles

These follow from the goal of feel, and they decide the arguments below.

1. **Never reimplement what Ghostty does.** Scrolling, search, selection, and
   viewport state belong to the terminal. Lime adds marks and intent.
2. **Every layer is useful alone.** Layer 1 works after lime exits. A user who
   never presses a key still gets section navigation.
3. **Degrade to something honest.** Where a capability is missing, lime says so
   or silently falls back to a mechanism that always works. It never presents a
   control that does nothing.
4. **No polling.** Redraw on keystroke and on resize. An idle reader costs no
   CPU. A poll loop against AppleScript would be perceptible and is disallowed.
5. **The document stays real terminal text.** Selection and ⌘F must work on
   every character lime prints, for the whole life of the session.

## Architecture

Three layers, each independently shippable.

### Layer 1 — semantic marks

During rendering, lime emits `OSC 133;A ST` immediately before each heading it
prints. Ghostty records a prompt mark at that screen row.

The payoff is native and needs no live process: Ghostty binds `jump_to_prompt:-1`
and `jump_to_prompt:1` to `super+arrow_up` and `super+arrow_down` by default, so
⌘↑ and ⌘↓ walk heading to heading through scrollback. This keeps working after
lime returns to the shell, and it composes with ⌘F and with selection.

This is the smallest change in the design and the largest share of the value.
It attaches at the existing heading branch in `render.py`, which already
special-cases `heading_open` at level 0 to draw image headings.

Marks are emitted for every heading H1–H6, including text-mode headings and
headings that fall back from failed image rendering, so navigation does not
depend on graphics being available.

### Layer 2 — the resident reader

After printing the document, lime does not exit. It reads `/dev/tty` in raw
mode and dispatches keys. Native scroll, selection, and ⌘F continue to work,
because Ghostty handles those itself and they never reach the process.

The raw-mode pattern already exists in `terminal.py:36` `read_palette`: open
`/dev/tty` separately from stdin so piped Markdown still works, clear `ICANON`
and `ECHO`, and restore the original `termios` attributes in a `finally` block.
The reader generalises that into a reusable context manager. Restoring terminal
state on every exit path, including `SIGTERM`, `SIGHUP`, and an uncaught
exception, is a correctness requirement, not a nicety: a reader that leaves the
terminal without echo has failed no matter how good it looked.

### Layer 3 — the jump

Selecting a heading asks Ghostty to move its own viewport, through
`perform action` on the surface lime is running in.

**Anchored mark-relative jumping is the primary mechanism.** Absolute row
arithmetic is avoided entirely. To reach heading index `i` of `n`:

```
perform action "scroll_to_bottom"
perform action "jump_to_prompt:-(n - i)"
```

Re-anchoring at the bottom makes the jump deterministic regardless of where the
user has scrolled by hand, which a bare relative `jump_to_prompt` cannot be.
Lime never needs to know its absolute position in scrollback, never needs a
baseline for content printed before it started, and never needs to predict
wrapped line counts or image heights.

`scroll_to_row` is a possible later refinement, contingent on measuring whether
its argument indexes the full scrollback buffer or the viewport, and on
establishing a baseline row. It is explicitly not required for this design.

Both actions are issued inside one synchronized-output frame where possible so
the re-anchor is not seen as a flash. If measurement shows the flash is still
perceptible, the fallback is the reprint strategy below, chosen by config.

#### Surface discovery

Lime must identify which terminal surface it occupies. Hardcoding an ID, as
`.build/check-preview.applescript` does, is not viable.

Lime sets the window title to a single-use nonce via `OSC 2`, asks Ghostty for
the terminal whose `name` matches that nonce, records the surface ID, and then
sets the real title. This is exact even with many windows, tabs, and splits
open, and it needs no new Ghostty capability. The ID is cached for the session.

If discovery fails, lime falls back to the frontmost window's focused terminal
only when exactly one Ghostty window exists; otherwise jumping is disabled and
reported, per principle 3.

#### Latency

Spawning `osascript` per jump costs roughly 50–150 ms, which is felt. Lime
instead starts one long-lived `osascript` process on first jump, holding it open
and writing commands to its stdin. Subsequent jumps avoid process startup. The
helper is terminated with the reader.

Jump latency has a budget of 50 ms from keypress to viewport movement. If the
persistent helper does not meet it, the design falls back to reprint.

#### Fallback: reprint

Where `perform action` is unavailable — Linux, a non-Ghostty terminal, an
unidentifiable surface — selecting a heading reprints that section at the bottom
of the document instead of moving the viewport. This always works, on every
platform, because printing is the one operation a terminal always honours, and
it lands the reader exactly where they asked to be.

It grows scrollback and creates duplicate ⌘F hits, so it is not the default
where a real jump is available. Config key `jump: auto | scroll | reprint`,
defaulting to `auto`.

## The control bar

A persistent bar and native scrollback are in genuine physical tension, and the
tension is not an implementation limit.

A row pinned with `DECSTBM` lives on the **active screen**. Scrolling up into
scrollback means viewing history, which is above the active screen; the pinned
row is below the viewport and not drawn. Any upward scroll hides it. No escape
sequence changes this, because it is what scrollback is.

Therefore the bar lives in the **window title**, set with `OSC 2`. Ghostty paints
the title regardless of scroll position, so it is visible while reading history —
the only option that is genuinely persistent without surrendering scrollback.

```
lime · README.md · Installation 3/8 · ? for keys
```

`DECSTBM` is rejected: it is invisible exactly when the reader is scrolled up,
which is most of a reading session, and it additionally depends on unverified
Ghostty behaviour about whether lines scrolled out of a region with top margin 1
reach scrollback. The alternate screen is rejected under the governing
constraint.

**Known limitation, stated plainly.** Lime receives no scroll events, so the
title cannot track native scrolling. The section shown is the last section lime
navigated to, not necessarily what is on screen after a manual scroll. Lime will
not poll for viewport position; that violates principle 4 and would feel worse
than the imprecision. Where no jump has occurred yet, the section field is
omitted rather than guessed:

```
lime · README.md · ? for keys
```

The original title is restored on exit.

## Overlays

The table of contents and the help modal paint at the bottom of the screen,
where the cursor already is.

Opening an overlay prints `h` newlines to reserve fresh rows, scrolling the
document up without destroying anything, then draws into that space. Closing
moves the cursor to the top of the reserved block and erases with `ESC[J`.
Nothing that was already printed is overwritten, so scrollback stays intact.
This is why overlays never need to restore covered content: they never cover
any.

Each redraw is wrapped in synchronized output (`DECSET 2026` / `DECRST 2026`),
which Ghostty supports, so filtering never tears.

Overlay height is `min(12, rows - 4)`, with a floor of 6 rows: a border pair, a
filter line, a separator, and at least two entries. When `rows - 4` is below 6,
the overlay is suppressed and lime reports that the window is too short, rather
than drawing something broken.

### Table of contents

Opened with `t`. The layout follows the mockup already in `docs/ideas.md`:

```text
╭─ Jump to section ───────────────────────────╮
│ > install                                   │
├─────────────────────────────────────────────┤
│ › Installation                         H2   │
│     Install with Homebrew              H3   │
│     Install from source                H3   │
│   Troubleshooting installation         H2   │
╰─ ↑/↓ move · Enter jump · Esc close ─────────╯
```

- Typing filters headings by subsequence match; matched characters are
  highlighted. Filtering is instant, with no debounce.
- `↑`/`↓` and `Ctrl-P`/`Ctrl-N` move the selection. Plain `j` and `k` are
  deliberately not bound, because they are filter text.
- `Enter` jumps and closes. `Esc` closes without moving.
- Entries indent by heading level and are labelled with their level.
- Selection is clamped, wraps at both ends, and survives filter edits where the
  selected entry still matches.
- With no headings at all, the overlay says so instead of opening empty.

Headings are identified by their token source position, not their text, so
duplicate titles in different chapters remain distinct entries and resolve to
different jump targets.

### Help modal

Opened with `?`, closed with `?`, `Esc`, or `q`. It lists every binding,
including the native Ghostty ones the user should know about, since discovering
⌘F and ⌘↑/⌘↓ is part of the feel:

```
  t          table of contents        ⌘F    search (Ghostty)
  n / p      next / previous section  ⌘↑/⌘↓ section jump (Ghostty)
  g / G      top / bottom             ⌘C    copy selection
  ?          this help
  q          quit
```

Bindings that are unavailable in the current session — jumping without a
resolved surface — are shown dimmed with a one-line reason.

## Key bindings

| Key | Action |
|---|---|
| `t` | open table of contents |
| `?` | toggle help |
| `n` / `p` | next / previous section, by the same anchored jump against lime's own current-section index — never a bare relative `jump_to_prompt`, which would drift after a manual scroll |
| `g` / `G` | scroll to top / bottom |
| `q`, `Ctrl-C`, `Ctrl-D` | quit |

Lime binds no scrolling keys beyond `g`/`G`, `n`/`p`. Arrow keys, page keys, and
the mouse wheel are left to Ghostty, unmodified, per principle 1.

## Mode selection

Interactive mode activates only when all of the following hold: stdout is a TTY,
`/dev/tty` opens, and Ghostty is detected without a multiplexer — the same
condition `Terminal.detect` already computes for `graphics`.

Piped, redirected, `NO_COLOR`, `--plain`, and non-Ghostty output keep the exact
v0.1 behaviour: print once and exit 0. This preserves `lime DOC.md > out.txt`,
`cat DOC.md | lime -`, and the existing test suite, which runs without a TTY.

`--interactive` forces the reader on where detection is conservative;
`--no-interactive` forces the v0.1 behaviour. Config key `interactive: auto`.

Layer 1 marks are emitted whenever output is a TTY, including when interactive
mode is off, since they cost nothing and survive exit.

## Components

New modules, each with one purpose and testable without a terminal:

| Module | Purpose | Depends on |
|---|---|---|
| `outline.py` | headings → outline model; subsequence filter | markdown-it tokens |
| `keys.py` | raw-mode context manager; byte stream → key events | `termios` |
| `overlay.py` | draw/erase picker and help; pure string generation | palette |
| `ghostty.py` | surface discovery, action helper, availability probe | `osascript` |
| `reader.py` | event loop and state machine | all of the above |

`render.py` gains OSC 133 emission and returns the outline it built. `cli.py`
gains mode selection and hands off to `reader.py`. No existing module grows a
second responsibility; `terminal.py` keeps its output-only role and lends its
`termios` pattern to `keys.py`.

## Data flow

```
source ──▶ render() ──▶ stdout (document + OSC 133 marks)
              │
              └────────▶ Outline[Section(title, level, index, position)]
                              │
                        reader loop ◀── keys.py ◀── /dev/tty (raw)
                              │
                    ┌─────────┼─────────────┐
                    ▼         ▼             ▼
               overlay.py  OSC 2 title   ghostty.py
               (draw/erase)              (scroll_to_bottom
                                          + jump_to_prompt:-Δ)
```

The outline is built once during rendering and never recomputed. Section index
`i` in the outline corresponds to mark `i` in Ghostty's ordering, which is what
makes the anchored relative jump correct.

## Error handling

- **Surface not resolved** — jumping disabled, TOC still opens as an outline,
  help shows the reason. Not an error exit.
- **`perform action` returns false or the helper dies** — fall back to reprint
  for the rest of the session, once, quietly. Retrying a failing bridge on every
  keypress would feel broken.
- **`osascript` missing (Linux)** — reprint mode from the start.
- **Terminal resize** — `SIGWINCH` re-measures and redraws an open overlay.
  Content already printed is not reflowed, matching v0.1 behaviour.
- **Window too short** — overlays suppressed with a message.
- **`SIGTERM` / `SIGHUP` / uncaught exception** — restore `termios`, restore the
  title, erase any open overlay, terminate the helper.
- **Existing `BrokenPipeError` and `KeyboardInterrupt` handling in `cli.py`** is
  preserved; `KeyboardInterrupt` inside the reader quits cleanly with 0 rather
  than 130, because there it is the documented quit key.

## Testing

Everything except the terminal bridge is a pure function and is tested as one.

- `outline.py` — extraction from tokens including duplicate titles, inline
  formatting in headings, headings inside lists and quotes, empty documents;
  subsequence filter ranking and match positions.
- `keys.py` — byte sequences to key events: arrows, `Ctrl-P`/`Ctrl-N`, `Esc`,
  UTF-8 multibyte input, and a bare `Esc` distinguished from an escape sequence
  by a short read timeout.
- `overlay.py` — exact emitted strings: reserved height, synchronized-output
  wrapper, `ESC[J` erase, indentation, highlighting, clamped and wrapping
  selection, suppression below minimum height.
- `render.py` — `OSC 133;A` precedes every heading, byte-exact, in image, text,
  and fallback paths; no marks when not a TTY.
- `ghostty.py` — action string construction and the `n - i` delta; the
  `osascript` boundary is injected so tests never spawn it.
- `reader.py` — the state machine driven by a scripted key list against a fake
  output stream: open TOC, filter, select, jump, close, help toggle, quit.
- `cli.py` — mode selection matrix, and that non-TTY output is byte-identical to
  v0.1 so the existing suite keeps passing unchanged.

Manual verification uses the existing `.build/` Ghostty capture tooling, which
already drives `perform action` and `write_scrollback_file`.

## Two measurements before implementation

Both are cheap and both gate a specific decision. Neither blocks writing the
implementation plan.

1. **Does Ghostty create a prompt mark from `OSC 133;A` emitted by a non-shell
   process?** Layer 1 depends on it entirely. Expected yes, since shell
   integration is exactly this mechanism, but it is the load-bearing assumption
   of the whole design and has not been observed. If no, layer 3 must use
   `scroll_to_row` with absolute row accounting, which `graphics.py:103` already
   makes possible by yielding row counts per heading image.
2. **Is the anchored re-anchor flash perceptible?** Decides whether `auto`
   resolves to `scroll` or `reprint` on macOS.

## Documentation

The README gains a section on interactive navigation, written for a developer
reading this project for the first time. Plain English, no insider shorthand.

- Spell out what a "prompt mark" is and why a Markdown reader emits one. A
  reader who has never heard of OSC 133 must still follow the paragraph.
- Explain the division of labour in one sentence a newcomer can repeat back:
  lime prints and marks, Ghostty scrolls and searches.
- Say why the control bar is in the window title rather than on the screen, in
  terms of what the reader can observe — scroll up and an on-screen bar would be
  gone, the title stays.
- State the tracking limitation directly. A reader must not have to discover by
  surprise that the title lags a manual scroll.
- Document every key, and name the Ghostty-native ones (⌘F, ⌘↑, ⌘↓) alongside
  lime's own, so the reader learns the whole set rather than half of it.
- Note the macOS-only jump and what Linux does instead.
- Avoid terms the surrounding README does not already establish. Where a term is
  unavoidable, define it on first use.

`docs/ideas.md` is updated in the same change: its claim that no arbitrary-row
jump exists is corrected, and the sections this design implements are marked as
shipped rather than proposed.

## Out of scope

Fuzzy scoring beyond subsequence matching, a horizontal paging mode, live reload
on file change, section-filtered output, search implemented inside lime, mouse
interaction, and Ghostty tab or window automation. Reading position is not
persisted across runs.

## What ships

Layer 1 alone is a complete, shippable improvement and should land first: ⌘↑ and
⌘↓ walking sections, working after exit, on every platform, in a few lines. Layers
2 and 3 build the reader on top of it. If the design has to stop early, it stops
somewhere useful.
