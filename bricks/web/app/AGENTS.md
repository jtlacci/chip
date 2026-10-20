# App contract

This folder is the whole web brick: a browser app in plain JavaScript modules, with no packages.

- `src/` holds the app. `build.js` copies it to `dist/`; the server serves `dist/`.
- Every network call goes through `src/api.js`. The lint fails any `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource` or `sendBeacon` in another file.
- The app reads and writes data only through the api brick's routes (see `bricks/api/contract.py`).
- The `data-*` attributes in `bricks/web/contract.py` are how the evaluation finds things. Keep them on the elements they name.
- Use only system fonts and local files: the evaluation runs with no network, so a font or image from another host fails to load.
- There are no tests here. The evaluation in `evaluation/` checks rendering at phone and computer widths, speed, layout shift, navigation, content above the fold, scrolling, typing, uploads and the agent's animation.
