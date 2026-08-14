# Almanac app icon — design brief

Status: **unresolved after two rounds by Claude.** Handed off — next attempt
should come from a different designer/tool (Codex), not another Claude pass.

## What's needed

A macOS app icon (`.icns`) for the Almanac desktop app (bundle name
`kb-core.app`, product name "Almanac"). Wired into the build already —
`packaging/kb-core.spec`'s `BUNDLE(... icon=...)` points at
`packaging/assets/almanac.icns`. Replacing that file and rebuilding is
enough; no other code changes needed.

## Brand constraints

- Background: dark navy, matching the web UI's `--bg` (`#091017`) / `--surface`
  (`#0d151d`).
- Accent: mint/teal, matching `--accent` (`#69e3ce`).
- macOS convention: square canvas, ~22% corner radius rounded square (system
  applies further masking on some versions, but the source should already
  look like a rounded square, not a full square or a circle).
- Must read clearly at both 512px (Launchpad/Finder icon view) and 32px (Dock)
  — the two sizes that actually get looked at. 16px (Finder list view)
  degrading to an unreadable blob is normal and not worth fighting.

## What's been tried and rejected

**Round 1** (abstract): an open-book/pages shape with a bookmark accent,
built from flat vector paths. Feedback: "睇落有啲怪，唔好咁抽象" — read as
odd/unclear, too abstract to parse as anything in particular.

**Round 2** (two literal options, offered side by side): (a) a closed book
viewed at an angle with a bookmark ribbon and page-edge highlights, (b) a
plain bold serif "A" monogram (the Notion/Obsidian move). Neither was picked;
user asked for the icon to be redesigned again rather than choosing between
them.

Source files for both rounds are not preserved (scratch dir, cleaned up) —
only the descriptions above and this outcome. Don't necessarily rule out
"a book" or "a monogram" as concepts; the specific executions didn't land,
which isn't the same as the concept being wrong.

## Open question for whoever picks this up

Two rounds of "geometric flat icon in the brand palette" didn't land. Worth
considering a genuinely different register before iterating on the same
approach again — e.g. a different metaphor entirely (not book, not
monogram), a different rendering style (less flat/vector, more depth or
texture), or getting the user's own reaction to *specific* reference icons
they like before generating new candidates blind.

## Output format

`packaging/assets/almanac.icns`, built via `iconutil -c icns` from a
`.iconset` folder with the standard macOS sizes (16/32/64/128/256/512,
@1x and @2x). Do not patch `Info.plist` inside an already-signed,
already-built `.app` bundle directly to preview — that invalidates the code
signature (confirmed the hard way: `spctl` started reporting `invalid
Info.plist (plist or signature have been modified)` until reverted). Preview
via rendered PNGs instead, and only apply the real icon through a proper
signed release build.
