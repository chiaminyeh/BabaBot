# AGENT Rules for BabaBot

> **Project scope**: BabaBot is the Discord bot repository. It does **not currently contain** the separate 求生意志 web game. A future `Baba SurvivingWill` mini-game or web link may be added later; do not assume that integration exists today.
>
> **Wiki-first guidance**: Before reading multiple source files or editing code, consult the [project Wiki](wiki/WIKI.md) when the task concerns architecture. The Wiki is an automatically generated architecture snapshot and may be stale; source code, tests, and runtime checks are authoritative.

## Agent workflow

- Preserve unrelated working-tree changes and never read or expose `.env` contents.
- Prefer repository-relative links so this project remains usable after cloning to another computer.
- Verify the exact symbol and callers before editing; run focused tests and compile checks after changes.
- Keep runtime data, logs, caches, player saves, and `.hermes/` out of Git.
- Do not claim that 求生意志 is implemented inside BabaBot unless the integration is actually present and tested.

## Guidelines
1. **TRPG System (求生意志)**: Located under `trpg/` package. The entry point cog is [trpg/cog.py](trpg/cog.py#L32).
2. **Combat Mechanics**: Managed by [TRPGCombatEngine](trpg/combat.py#L25) and [choose_monster_action](trpg/monster_ai.py#L120).
3. **Character Progression & Stats**: Controlled by [recalc_player_stats](trpg/stats.py#L45) and stored in SQLite via [PlayerDatabase](trpg/player_db.py#L12).
4. **Discord UI & Views**: Handled in [trpg/view.py](trpg/view.py#L125), [trpg/view_dungeon.py](trpg/view_dungeon.py#L17), and [trpg/view_shop.py](trpg/view_shop.py#L29).
5. **Localization**: Always use [i18n.t() or i18n.tf()](trpg/i18n.py#L34) when outputting user-facing text to maintain English & 繁體中文 support.
