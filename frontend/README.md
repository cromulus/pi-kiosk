# Kiosk edge navigation

`swipe-navigation.upstream.js` is the installed hass-swipe-navigation v1.16.1
snapshot. `scripts/build_edge_swipe.py` builds `swipe-navigation.edge.js` with
an edge policy for browser URLs containing `kiosk=true`. Other sessions retain
normal swipe navigation.

A contact must begin within 20 CSS pixels of the left or right edge, continue
inward at least 260 CSS pixels, and travel horizontally at least twice as far
as vertically. The existing plugin distance setting also applies. Navigation
happens on release. Offscreen contact cannot be measured. Interior gestures do
not call preventDefault; cancellation and multiple contacts clear state.

Build and check:

```sh
python3 scripts/build_edge_swipe.py
node --check frontend/swipe-navigation.edge.js
node tests/test_edge_swipe.cjs
```

Deployed 2026-10-13 to Home Assistant at `/config/www/community/hass-swipe-navigation/swipe-navigation.js`
and its `.gz` companion. The original pair is backed up at
`/config/backups/pi-kiosk-edge-20261013/`. Home Assistant's served JavaScript
was downloaded and matched byte for byte, then Chromium on `pidisplay` was
sent Ctrl+F5. Physical touchscreen feel still needs user verification.

HACS updates can overwrite this patch. Reapply both JS and gzip together after
an update; rebuild against a reviewed new upstream snapshot when upgrading.
For rollback, restore both backed-up files and hard-refresh Chromium. A copy
of the patched file also exists at `/config/www/pi-kiosk-edge-navigation.js`,
but the resource URL remains the original HACS URL (the API account did not
have permission to change the resource registration).

Refresh behavior: on a browser reload in a `kiosk=true` session, the frontend
returns to `/lovelace/kiosk`, preserves query options (including kiosk mode and
browser identity), and removes any anchor. Normal navigation and non-kiosk
sessions are unaffected. Reloading the home URL does not redirect again. This
covers the top-edge gesture because the sensor daemon sends F5. The home path
is specified in `scripts/build_edge_swipe.py`; update it if the HA kiosk view
moves. The pre-change deployed JS/gzip pair is backed up at
`/config/backups/pi-kiosk-refresh-home-20261013/`. Routing checks and served-file
comparison passed; Chromium was hard-refreshed to load the change.
