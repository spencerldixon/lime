# Validation on 9 September 2026

- 57 automated checks pass, covering Markdown rendering, code wrapping and line
  numbers, narrow tables, YAML validation, padding on all sides, image placement,
  Mermaid failures, palette queries, and terminal-mode restoration.
- Ruff checks pass. The generated Homebrew formula passes Ruby syntax checking.
- The built wheel is installed with uv; the resulting `lime` executable passes
  a smoke test with a real Markdown file and clean redirected output.
- Mermaid CLI rendered a real diagram through the installed Chrome. Its PNG was
  visually inspected. The optional CLI was installed only under `.build/mermaid`
  for validation; users need `mmdc` on PATH for automatic diagrams.
- A preview ran in Ghostty 1.3.1. Exported native scrollback contains the real
  labels `# A little more lime` and `## Everything in its place`, plus numbered
  code and table text. The native search action was accepted.
- macOS window capture failed, so visual confirmation of the full Ghostty view,
  search highlights, and trackpad behaviour remains a manual check. A screenshot
  was requested from the user. Source/protocol checks are not a substitute for it.
- Homebrew accepted the local formula and downloaded its resources but stopped
  before installation because the machine's Command Line Tools are outdated.
  Homebrew installation and `brew test lime/local/lime` remain unverified. The
  installer can be rerun after the required toolchain update. Homebrew developer
  mode was restored to its previously disabled state.

Suggested manual check: run `lime examples/demo.md`, search for "Everything in
its place", scroll from start to finish, and inspect padding, code borders,
enlarged headings, and the image caption. Install Mermaid CLI to check the fenced
diagram as an image. Resize the window and rerun lime to recalculate the layout.
