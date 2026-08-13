# Agent Rules for BabaBot

> **Project scope**: BabaBot is the Discord bot repository. It does **not currently contain** the separate 求生意志 web game. A future `Baba SurvivingWill` mini-game or web link may be added later; do not assume that integration exists today.
>
> **Architecture guidance**: For architecture-oriented tasks, consult the [project Wiki](wiki/WIKI.md). The Wiki is an automatically generated architecture snapshot and may be stale; source code, tests, and runtime checks are authoritative.

## Agent workflow

- Preserve unrelated working-tree changes and never read or expose `.env` contents.
- Prefer repository-relative links so this project remains usable after cloning to another computer.
- Verify the exact symbol and callers before editing; run focused tests and compile checks after changes.
- Keep runtime data, logs, caches, player saves, and `.hermes/` out of Git.
- Do not claim that 求生意志 is implemented inside BabaBot unless the integration is actually present and tested.

## Project guidelines

1. **Baba TRPG**: Located under `trpg/`; its entry-point cog is `trpg/cog.py`.
2. **Combat mechanics**: Managed by `TRPGCombatEngine` in `trpg/combat.py`; monster decisions live in `trpg/monster_ai.py`.
3. **Character progression and stats**: Recalculated in `trpg/stats.py` and persisted through `PlayerDatabase` in `trpg/player_db.py`.
4. **Discord UI and views**: Implemented in `trpg/view.py` and the focused `trpg/view_*.py` modules.
5. **Localization**: Use `i18n.t()` or `i18n.tf()` from `trpg/i18n.py` for every player-facing string so English and 繁體中文 remain aligned.
