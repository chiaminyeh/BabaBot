# BabaBot

**A Discord game platform built around Baba TRPG, a turn-based RPG with
dungeons, 97 monsters, four character classes, and weekly live events, plus
poker, blackjack, Wordle, chess, and music.**

BabaBot runs 24/7 on a home server for a private Discord community. Players
create characters, explore 14 areas, fight monsters with distinct AI behaviors,
complete quests, and compete in rotating weekly events, all through Discord
buttons, menus, and embeds. The whole game is bilingual (English / 繁體中文).

<!-- TODO: add a 10–20 second GIF of a TRPG battle here, e.g.
![Baba TRPG battle](docs/media/trpg-battle.gif) -->

| Baba TRPG | Poker | Wordle |
| :---: | :---: | :---: |
| <!-- docs/media/trpg.png --> _screenshot_ | <!-- docs/media/poker.png --> _screenshot_ | <!-- docs/media/wordle.png --> _screenshot_ |

## Baba TRPG

| Content | Count |
| --- | ---: |
| Monsters | 97 |
| Distinct monster AI behaviors | 31 |
| Items | 161 |
| Skills | 38 |
| Areas | 14 |
| Quests | 30 |
| Achievements | 27 |
| Dungeon relics | 16 |
| Status effects | 10 |
| Weekly events | 8 |

- **Combat engine:** turn-based combat with action points, status effects
  (poison, burn, freeze, ...), and multi-monster encounters
  (`trpg/combat.py`).
- **Monster AI:** 31 behavior patterns, including healers, bodyguards,
  berserkers, kamikaze bombers, thieves, phase shifters, and a demon-lord
  boss who summons imps and hides behind them (`trpg/monster_ai.py`).
- **Classes:** Warrior, Rogue, Mage, and Warlock, each with its own core
  abilities and stat scaling (`trpg/archetypes.py`, `trpg/stats.py`).
- **Balance tooling:** deterministic simulations compare geared class
  performance at levels 5, 10, 20, 50, and 80
  (`scripts/simulate_archetype_balance.py`, `scripts/simulate_bee_queen_balance.py`).
- **Live ops:** rotating weekly events with featured monsters and reward or
  drop multipliers (`trpg/weekly_events.py`, `trpg_data/weekly_events.json`).
- **Data-driven content:** monsters, items, skills, quests, and events live in
  JSON under `trpg_data/`, so content changes need no code changes. A
  validator checks every cross-reference (`scripts/validate_json_references.py`).
- **Localization:** every player-facing string goes through `trpg/i18n.py`.
  `check_translations.py` keeps English and 繁體中文 aligned.

## Other games and features

- **Texas Hold'em poker** with side pots, configurable modes and pacing, and
  rule-based AI opponents (`poker/`, `poker_ai_strategy.py`).
- **Blackjack** and a **lottery**, using a shared virtual currency
  (BabaBucks) with transaction-safety tests.
- **Wordle**, daily and unlimited.
- **Chess** through a Discord Activity launcher, with Lichess open challenges
  as a fallback.
- **Music player** for a local library, using FFmpeg.
- **Scheduling, reminders, and time zones.**
- **AI chat responses** through an optional Gemini fallback.

## Engineering

- About 35,000 lines of Python on [discord.py](https://github.com/Rapptz/discord.py).
- 40 unit and regression test files covering combat, progression, the poker
  rules engine, currency transactions, and UI flows.
- GitHub Actions CI runs compile checks, tests, translation checks, and JSON
  reference validation on every push.
- Operational tooling for the 24/7 deployment: a health check, an error
  watcher, a restart script, and a monitor cog that reports to a Discord channel.

## Run it yourself

### Requirements

- Python 3.11 or newer (the CI workflow uses Python 3.11).
- FFmpeg on `PATH` for music playback. If it is not on `PATH`, set
  `BABABOT_FFMPEG_EXECUTABLE` to the full executable path in `.env`.
- A Discord bot application with the privileged intents enabled. `main.py`
  requests all intents, so enable the matching switches in the Discord
  Developer Portal.

### Setup

```bash
git clone https://github.com/chiaminyeh/BabaBot.git
cd BabaBot
python -m venv .venv
```

Activate the virtual environment, then install dependencies:

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS/Linux (use this instead on those systems)
# source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in at least `DISCORD_TOKEN`:

```powershell
Copy-Item .env.example .env
```

`GEMINI_API_KEY` enables the Gemini fallback used by the AI response cog.
`BABABOT_OWNER_ID` and the monitor channel variables are optional. Music files
default to `~/Music`; set `BABABOT_MUSIC_FOLDER` to use another library folder.

Start the bot with:

```bash
python main.py
```

Never commit `.env` or live runtime data. The repository `.gitignore` already
excludes credentials, logs, player saves, caches, and other local state.

### Development checks

```bash
python -m compileall -q .
python -m unittest discover tests
python check_translations.py
```

## Repository layout

- `main.py`: Discord bot entry point and extension loading.
- `trpg/`: TRPG cog, combat, monster AI, progression, views, and persistence.
- `trpg_data/`: game-content JSON. Player save files are ignored.
- `poker/`: poker domain, rules, and UI modules.
- `scripts/`: balance simulations, validators, and operational tools.
- `tests/`: unit and regression tests.
- `.github/workflows/ci.yml`: automated checks.
