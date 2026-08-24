# Helix styles

`index.css` is the only stylesheet imported by React. It pulls the rest in cascade order.

- `foundation.css` – base elements, generic cards/forms/layout primitives
- `player.css` – playback bar and audio controls
- `playlists.css` / `playlist-polish.css` – playlist pages and editor
- `stations.css`, `station-config.css`, `station-seed.css` – station UI
- `search.css` – search page
- `library-detail-settings.css` – artist/album/detail/settings-era shared styles
- `lobbies.css` – lobby views
- `home.css` – home control center
- `ui-actions.css` – shared compact actions and small component polish
- `shell-queue.css` – sidebar/account/queue shell refinements
- `history.css` – history page
- `tokens.css` – current theme variables; this is the first place to edit colors
- `theme-amber.css` – final application-wide dark theme rules

The files intentionally remain imported in the same cascade order as the former monolithic `styles.css`, so this refactor changes organization without intentionally changing appearance.
