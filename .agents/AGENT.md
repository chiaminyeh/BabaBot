# AGENT Rules & Token Optimization Guidelines for BabaBot & 求生意志

> **TOKEN OPTIMIZATION & WIKI FIRST RULE**:
> Before reading multiple source files or editing code in this codebase, you **MUST inspect the Project Wiki ([wiki/WIKI.md](file:///c:/Users/manza/Downloads/bot/Bot/wiki/WIKI.md)) FIRST**.
> The wiki provides exact architecture maps, cross-module relationships, and function-level line links (e.g. `[function_name](file:///path/to/file#L123)`). Use it to jump directly to target implementations to conserve tokens and avoid loading excessive context.

## Guidelines
1. **TRPG System (求生意志)**: Located under `trpg/` package. The entry point cog is [trpg/cog.py](file:///c:/Users/manza/Downloads/bot/Bot/trpg/cog.py#L32).
2. **Combat Mechanics**: Managed by [TRPGCombatEngine](file:///c:/Users/manza/Downloads/bot/Bot/trpg/combat.py#L25) and [choose_monster_action](file:///c:/Users/manza/Downloads/bot/Bot/trpg/monster_ai.py#L120).
3. **Character Progression & Stats**: Controlled by [recalc_player_stats](file:///c:/Users/manza/Downloads/bot/Bot/trpg/stats.py#L45) and stored in SQLite via [PlayerDatabase](file:///c:/Users/manza/Downloads/bot/Bot/trpg/player_db.py#L12).
4. **Discord UI & Views**: Handled in [trpg/view.py](file:///c:/Users/manza/Downloads/bot/Bot/trpg/view.py#L125), [trpg/view_dungeon.py](file:///c:/Users/manza/Downloads/bot/Bot/trpg/view_dungeon.py#L17), and [trpg/view_shop.py](file:///c:/Users/manza/Downloads/bot/Bot/trpg/view_shop.py#L29).
5. **Localization**: Always use [i18n.t() or i18n.tf()](file:///c:/Users/manza/Downloads/bot/Bot/trpg/i18n.py#L34) when outputting user-facing text to maintain English & 繁體中文 support.
