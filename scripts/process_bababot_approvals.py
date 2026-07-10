from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
STATE_PATH = LOG_DIR / "watchdog_approvals_state.json"
INCIDENTS_PATH = LOG_DIR / "watchdog_incidents.json"
OWNER_ID = os.getenv("BABABOT_OWNER_ID", "295288056276189185")
TOKEN_ENV = "DISCORD_TOKEN"
COMMAND_RE = re.compile(r"^(fix|skip)\s+(inc-[a-z0-9-]+)\s*$", re.IGNORECASE)
ACTION_ONLY_RE = re.compile(r"^(fix|skip)\s*$", re.IGNORECASE)
INCIDENT_IN_TEXT_RE = re.compile(r"(inc-[a-z0-9-]+)", re.IGNORECASE)


def load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def save_json(path: Path, payload: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def discord_api_request(url: str, token: str, payload: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any] | list[dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "bababot-watchdog/1.1",
        },
        method=method,
    )
    with request.urlopen(req, timeout=20) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def get_dm_channel_id(token: str, state: dict[str, Any]) -> str:
    channel_id = state.get("dm_channel_id")
    if channel_id:
        return str(channel_id)
    dm = discord_api_request(
        "https://discord.com/api/v10/users/@me/channels",
        token,
        {"recipient_id": OWNER_ID},
    )
    channel_id = str(dm["id"])
    state["dm_channel_id"] = channel_id
    return channel_id


def fetch_messages(token: str, channel_id: str, after: str | None) -> list[dict[str, Any]]:
    query = "?limit=50"
    if after:
        query += f"&after={parse.quote(str(after))}"
    data = discord_api_request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages{query}",
        token,
        payload=None,
        method="GET",
    )
    assert isinstance(data, list)
    return list(reversed(data))


def send_ack(token: str, channel_id: str, message: str) -> None:
    discord_api_request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        token,
        {"content": message},
    )


def extract_incident_id_from_message(message: dict[str, Any]) -> str | None:
    content = (message.get("content") or "").strip()
    match = INCIDENT_IN_TEXT_RE.search(content)
    if match:
        return match.group(1)

    referenced = message.get("referenced_message")
    if isinstance(referenced, dict):
        referenced_content = (referenced.get("content") or "").strip()
        match = INCIDENT_IN_TEXT_RE.search(referenced_content)
        if match:
            return match.group(1)

    return None


def main() -> int:
    load_environment()
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is not set")

    state = load_json(STATE_PATH, {"last_seen_message_id": None, "dm_channel_id": None})
    incidents = load_json(INCIDENTS_PATH, [])
    incident_map = {item.get("id"): item for item in incidents}
    channel_id = get_dm_channel_id(token, state)

    try:
        messages = fetch_messages(token, channel_id, state.get("last_seen_message_id"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(f"Discord API error: {exc.code} {body}")
        return 1

    changed = False
    for message in messages:
        state["last_seen_message_id"] = message.get("id")
        author = message.get("author", {})
        if str(author.get("id")) != OWNER_ID:
            continue
        content = (message.get("content") or "").strip()
        match = COMMAND_RE.match(content)
        action_only_match = ACTION_ONLY_RE.match(content)
        if not match and not action_only_match:
            continue

        if match:
            action = match.group(1).lower()
            incident_id = match.group(2)
        else:
            action = action_only_match.group(1).lower()
            incident_id = extract_incident_id_from_message(message)
            if not incident_id:
                send_ack(token, channel_id, "這則回覆裡找不到 incident id。請直接回 `fix inc-...`，或用 reply 回覆含 incident 的 watchdog 訊息並只寫 `fix` / `skip`。")
                continue

        incident = incident_map.get(incident_id)
        if not incident:
            send_ack(token, channel_id, f"找不到 incident `{incident_id}`。")
            continue

        if action == "fix":
            if incident.get("status") in {"approved", "in_progress", "resolved"}:
                send_ack(token, channel_id, f"incident `{incident_id}` 目前狀態是 `{incident.get('status')}`，不重複送修。")
                continue
            incident["status"] = "approved"
            incident["approved_at"] = utc_now()
            incident["approval_message_id"] = message.get("id")
            send_ack(token, channel_id, f"已收到 `fix {incident_id}`。Hermes 會開始修復並在完成後回報。")
            changed = True
        elif action == "skip":
            incident["status"] = "skipped"
            incident["skipped_at"] = utc_now()
            incident["approval_message_id"] = message.get("id")
            send_ack(token, channel_id, f"已忽略 incident `{incident_id}`。")
            changed = True

    if changed:
        save_json(INCIDENTS_PATH, incidents)
    save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
