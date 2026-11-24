# Helix CSS maintenance guidelines

## Ownership

1. Every selector has one logical owner. Modify its existing owner rather than adding an override file.
2. Create a new stylesheet only for a standalone page, a genuinely reusable component,
   or an independent feature. Small controls belong in their parent feature stylesheet.
3. When editing an existing rule, change the original declaration. Do not append
   `*-fix.css`, `*-polish.css`, `*-v2.css`, or similar files.
4. Keep responsive rules with the component when possible. `responsive-shell.css`
   is reserved for cross-page app shell / layout adjustments.
5. Use Helix theme variables and the configured font. Avoid hard-coded accent colors.
6. Avoid new `!important` declarations; prefer well-scoped component selectors.
7. Keep selectors, declarations, and media queries readable: one property per line,
   descriptive comments, and consistent two-space indentation.
8. `index.css` is import-only. Add imports at their intentional cascade position.
   Do not import a stylesheet twice or depend on an arbitrary "last override".

## Why some separate files remain

Not all CSS is globally imported. The Helix pages also import some stylesheets
individually (e.g. Quality Upgrades, Shares, and playlist creation). Their
filenames remain intact to keep those imports working without requiring TSX
changes. Likewise, independent dormant/optional styles are preserved intact.

The established cascade is a behavior contract. Some large stylesheets retain
historical overrides (`!important` and repeated selectors). Removing those
safely requires visual comparison across themes, playbar variants, and viewport
sizes. Do not mechanically flatten them into the last declaration: different
specificities, states, and breakpoints can make apparently redundant rules
significant.

## Source-preserving consolidation in this pass

- `playlist-polish.css` and `playlist-menu-fix.css` were joined directly after
  `playlists.css`, in their original import order.
- The adjacent desktop queue/full-bleed and main-content gutter overrides are
  now in `shell-layout.css`, at the exact same import position.
- `station-tune-fix.css` has a descriptive name, `station-tune-layout.css`,
  and remains late in the cascade to preserve its effective behavior.
- The viewport rules that were written after the imports in `index.css` now
  appear at the end of the final imported stylesheet, `responsive-shell.css`.

## Recommended validation after installation

Compare Home, Search, Liked Songs, Playlists, Stations, Quality Upgrades,
Lobbies, Shares, and Big Picture in all active themes; test each playbar style
at wide, narrow, and portrait viewports. This pass was validated by source rule
order and value equivalence, but no browser screenshot regression was run.
