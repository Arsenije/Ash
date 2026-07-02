# Changelog

All notable changes to Ash. Dates are release dates.

## 0.3.0 — 2026-07-02

- **Import from Immich** (Archie, [#13](https://github.com/Arsenije/Ash/pull/13)) — connect Ash to a self-hosted [Immich](https://immich.app) server, pick albums, and pull them into your local library where they're described and searchable like any other photo — offline, originals kept on your machine.
- **Sharper vision, configurable connections** (Damir Krstanovic, [#9](https://github.com/Arsenije/Ash/pull/9), [#11](https://github.com/Arsenije/Ash/pull/11)) — a single user-selectable vision-language model (Qwen2.5-VL) now both describes photos and finds connections, with a configurable entity ontology.
- **Steadier imports and setup** (Damir Krstanovic, [#8](https://github.com/Arsenije/Ash/pull/8), [#10](https://github.com/Arsenije/Ash/pull/10)) — cleaner photo descriptions, per-phase timeouts and a circuit breaker so a stuck model can't stall an import, and a simpler dependency setup.

## 0.2.0 — 2026-06-18

- **Safer and smarter search** (Damir Krstanovic, [#5](https://github.com/Arsenije/Ash/pull/5)) — tightened security around your local library and improved search so results are more relevant and surface related photos you'd otherwise miss.
- **Search by place** (Aleksandar Ristic, [#4](https://github.com/Arsenije/Ash/pull/4)) — photos with GPS data now show where they were taken, and you can search, filter, and group them by city and country — all offline.
