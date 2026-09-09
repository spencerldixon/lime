# Beyond the first version

These are proposals for discussion, not implemented features.

## A keyboard-driven heading picker

An interactive reader should have a Telescope-style table of contents. Press
`t` to open a centred modal over the document:

```text
╭─ Jump to section ───────────────────────────╮
│ > install                                  │
├────────────────────────────────────────────┤
│ › Installation                         H2  │
│     Install with Homebrew              H3  │
│     Install from source                H3  │
│   Troubleshooting installation         H2  │
╰─ ↑/↓ move · Enter jump · Esc close ─────────╯
```

- Type to fuzzy-filter headings; highlight matched letters.
- Navigate with arrows or Ctrl-J / Ctrl-K, since plain `j` and `k` belong in the
  filter text. Enter jumps; Escape closes the picker without moving the reader.
- Indent by heading level, show the current section, and include parent context
  for repeated names such as "Installation" in different chapters.
- Use terminal foreground, background, and palette colours; give the modal a
  clear border and restore the reading position when dismissed.
- Store heading identity using parser source positions, so duplicate titles and
  headings containing inline formatting remain distinct.

This belongs to a persistent interactive mode: after v0.1 returns to the shell,
`t` is shell input. An optional `lime --read DOC.md` could keep the reader alive,
with `q` returning to the shell. This is a proposed command, not a current flag.

The picker is straightforward; moving to the selected heading while preserving
native scrollback is the architectural question. Terminal escape sequences do
not offer a portable arbitrary-row jump into Ghostty's native scrollback. A
reader that owns the document viewport can implement precise jumps, but then it
must also implement document-wide navigation/search. A Ghostty-specific native
integration would need separate investigation. Do not claim the current
scrollback reader can gain this simply by adding a key binding.

## A horizontal reading mode

`lime --pages DOC.md` could treat each H2 as a chapter. A document's opening H1
and introductory content become its cover; H3–H6 remain within their chapter.
For a document with no H2, use H1 boundaries. A later `--section-level` option
could make that choice explicit.

- Left/right move between chapters; scrolling reads within the current chapter.
- Keep each chapter's scroll position when moving away and back.
- Show a quiet `3 / 8 · Installation` indicator and an outline on demand.
- Honour resize and provide a no-motion setting.
- Use a short, roughly 120 ms horizontal slide with an ease-out curve. It should
  reinforce where the next section came from and stop moving once settled.

The central engineering choice is who owns the viewport. A full-screen reader
can intercept arrows and draw transitions, but then document-wide search and
vertical navigation need their own implementation. Ghostty's native search sees
what exists in its terminal buffer, not the unrendered Markdown chapters.
It cannot automatically become document search across pages that aren't there.

An alternative is a separate Ghostty tab for each chapter, retaining native
scroll/search inside each one. That would need Ghostty-specific window/tab
automation and would make per-document state more complicated. I would prototype
the first approach as an optional mode while keeping the scrollback reader as
the default.

Ghostty supports synchronized terminal updates, which could make text-based
transition frames less prone to tearing. That is a building block, not a native
page-animation API or a guarantee of smooth scrolling. A prototype should
measure frame pacing with actual image headings before committing to a slide.
Rasterising entire pages would compromise selection/search, so settled page
content should remain terminal text.

## Other features worth discussing

1. **Richer image navigation.** Local PNG/JPEG/WebP and Mermaid are now in v0.1.
   A future zoom action could help inspect large diagrams without losing place.
2. **A reading tab or Quick Terminal action.** Open a document in its own Ghostty
   surface, keeping it separate from build logs and shell commands. macOS
   AppleScript support makes this a plausible integration to explore.
3. **An outline and section filter.** `lime --section "Installation" DOC.md` gives
   a focused slice while preserving native scrollback. Useful before a full pager.
4. **A split beside your editor.** A watch mode could refresh on save, with a
   clear decision about replacing the view versus appending revisions to history.
5. **Native sized text when available.** Adopt OSC 66 once Ghostty implements it,
   potentially removing both raster headings and their duplicate text labels.

For a second version, section filtering would deliver useful functionality with
less architectural change than an animated reader.

References: [Ghostty terminal features](https://ghostty.org/docs/features),
[native search](https://ghostty.org/docs/install/release-notes/1-3-0),
[palette queries](https://ghostty.org/docs/vt/osc/4), and
[the text-sizing request](https://github.com/ghostty-org/ghostty/issues/10333).
