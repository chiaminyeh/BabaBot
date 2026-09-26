"""Deterministic weekly themes, personal progress, and small rotating bonuses."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from trpg.i18n import t, tf


def utc_today(now: date | datetime | None = None) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(timezone.utc).date()
    return now


def week_key(now: date | datetime | None = None) -> str:
    day = utc_today(now)
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def current_weekly_event(catalog, now: date | datetime | None = None) -> dict:
    """Rotate catalog entries every Monday at 00:00 UTC without mutable state."""
    events = list(catalog or [])
    if not events:
        return {}
    monday = utc_today(now) - timedelta(days=utc_today(now).weekday())
    return events[(monday.toordinal() // 7) % len(events)]


def ensure_weekly_progress(player, event: dict, now: date | datetime | None = None) -> dict:
    real = getattr(player, "real_player", player)
    key = f"{week_key(now)}:{event.get('id', 'weekly')}"
    progress = getattr(real, "weekly_event_progress", None)
    if not isinstance(progress, dict) or progress.get("week_key") != key:
        progress = {"week_key": key, "kills": 0, "rewarded": False}
        real.weekly_event_progress = progress
    progress["kills"] = max(0, int(progress.get("kills", 0) or 0))
    progress["rewarded"] = bool(progress.get("rewarded", False))
    return progress


def is_featured_monster(event: dict, monster_id: str | None) -> bool:
    if event.get("featured_all"):
        return bool(monster_id)
    return bool(monster_id and monster_id in set(event.get("featured_monsters") or []))


def reward_multipliers(event: dict, monster_id: str | None) -> tuple[float, float]:
    if not is_featured_monster(event, monster_id):
        return 1.0, 1.0
    return (
        max(1.0, float(event.get("featured_gold_multiplier", 1.0) or 1.0)),
        max(1.0, float(event.get("featured_exp_multiplier", 1.0) or 1.0)),
    )


def drop_multiplier(event: dict, monster_id: str | None) -> float:
    if not is_featured_monster(event, monster_id):
        return 1.0
    return max(1.0, float(event.get("featured_drop_multiplier", 1.0) or 1.0))


def exploration_event_bonus(event: dict) -> float:
    return max(0.0, min(0.5, float(event.get("exploration_event_bonus", 0.0) or 0.0)))


def featured_spawn_weights(monster_ids: list[str], weights: list[float], event: dict) -> list[float]:
    multiplier = max(1.0, float(event.get("featured_spawn_multiplier", 1.0) or 1.0))
    return [weight * multiplier if is_featured_monster(event, monster_id) else weight for monster_id, weight in zip(monster_ids, weights)]


def record_weekly_kills(player, event: dict, count: int, now: date | datetime | None = None) -> tuple[dict, bool]:
    progress = ensure_weekly_progress(player, event, now)
    goal = max(1, int(event.get("goal_kills", 5) or 5))
    was_complete = progress["kills"] >= goal
    progress["kills"] = min(goal, progress["kills"] + max(0, int(count)))
    newly_complete = not was_complete and progress["kills"] >= goal and not progress["rewarded"]
    return progress, newly_complete


def weekly_event_field(player, catalog, lang: str, now: date | datetime | None = None) -> tuple[str, str] | None:
    event = current_weekly_event(catalog, now)
    if not event:
        return None
    progress = ensure_weekly_progress(player, event, now)
    goal = max(1, int(event.get("goal_kills", 5) or 5))
    title = tf(event, "title", lang) or event.get("id", "Weekly Event")
    description = tf(event, "description", lang) or ""
    bonus = tf(event, "bonus", lang) or ""
    field_name = t(lang, "weekly.field_name", "🌟 本週事件：{title}", title=title)
    if progress["rewarded"]:
        progress_line = t(lang, "weekly.progress_complete", "✅ 本週目標已完成（{goal}/{goal}）", goal=goal)
    else:
        progress_line = t(lang, "weekly.progress", "🎯 本週討伐進度：{kills}/{goal}", kills=progress["kills"], goal=goal)
    reset_line = t(lang, "weekly.reset", "⏳ 每週一 00:00 UTC 輪替")
    value = "\n".join(part for part in (description, progress_line, f"✨ {bonus}" if bonus else "", reset_line) if part)
    return field_name[:256], value[:1024]
