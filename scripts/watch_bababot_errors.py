from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
STATE_PATH = LOG_DIR / "watchdog_state.json"
INCIDENTS_PATH = LOG_DIR / "watchdog_incidents.json"
MAIN_PATH = (REPO_ROOT / "main.py").resolve()
OWNER_ID = os.getenv("BABABOT_OWNER_ID", "295288056276189185")
TOKEN_ENV = "DISCORD_TOKEN"
ERROR_MARKERS = (
    "traceback (most recent call last):",
    "[error",
    "interaction_failure",
    "ignoring exception in view",
    "ignoring exception in modal",
    "failed to load extension",
    "extensionfailed",
    "modulenotfounderror",
    "commandinvokeerror",
)
INTERACTION_SIGNATURES = (
    "interaction_failure",
    "ignoring exception in view",
    "ignoring exception in modal",
    "discord.ui.view",
    "discord.ui.modal",
)
IGNORED_COMMAND_SIGNATURES = (
    "commandnotfound",
    "command is not found",
    "ignoring exception in command none",
)
RECONNECT_SIGNATURES = (
    "attempting a reconnect",
    "reconnecting",
    "successfully resumed session",
    "websocket closed with 1006",
    "wsserverhandshakeerror: 503",
)
TRANSIENT_INTERACTION_SIGNATURES = (
    "unknown interaction",
    "serverdisconnectederror",
    "clientconnectordnserror",
)
MONITOR_DELIVERY_SIGNATURES = (
    "serverdisconnectederror",
    "clientconnectordnserror",
    "clientconnectorerror",
    "connectionreseterror",
    "timeouterror",
)
MAX_HASHES = 100
MAX_TRACKED_FILES = 12

# monitor_cog 每兩分鐘會在 Bot 行程內呼叫本模組。Windows 若直接啟動 wmic，
# 即使有 capture_output 仍可能短暫建立主控台視窗，因此明確要求不建立視窗。
WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def load_health_module():
    script_path = REPO_ROOT / "scripts" / "check_bababot_health.py"
    spec = importlib.util.spec_from_file_location("check_bababot_health", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load health checker from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def default_state() -> dict[str, Any]:
    return {
        "offsets": {},
        "seen_hashes": [],
        "bot_running": None,
        "last_alert_at": None,
        "dm_channel_id": None,
    }


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


def load_state() -> dict[str, Any]:
    return load_json(STATE_PATH, default_state())


def save_state(state: dict[str, Any]) -> None:
    save_json(STATE_PATH, state)


def load_incidents() -> list[dict[str, Any]]:
    return load_json(INCIDENTS_PATH, [])


def save_incidents(incidents: list[dict[str, Any]]) -> None:
    save_json(INCIDENTS_PATH, incidents)


def list_python_processes() -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "wmic",
            "process",
            "where",
            "name='python.exe' or name='pythonw.exe'",
            "get",
            "ProcessId,CommandLine",
            "/format:list",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        creationflags=WINDOWS_NO_WINDOW,
    )
    normalized = result.stdout.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    current_command: str | None = None
    processes: list[dict[str, Any]] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("CommandLine="):
            current_command = line.split("=", 1)[1].strip()
            continue
        if line.startswith("ProcessId=") and current_command:
            pid_text = line.split("=", 1)[1].strip()
            try:
                pid = int(pid_text)
            except ValueError:
                current_command = None
                continue
            processes.append({"pid": pid, "command_line": current_command})
            current_command = None
    return processes


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def is_bababot_running() -> bool:
    target = normalize_path(str(MAIN_PATH))
    for process in list_python_processes():
        if target in normalize_path(process.get("command_line", "")):
            return True
    return False


def candidate_logs() -> list[Path]:
    candidates: list[Path] = []
    for path in sorted(LOG_DIR.glob("bababot_restart_*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        candidates.append(path)
    for extra in (REPO_ROOT / "main_stderr.log", REPO_ROOT / "restart_err.tmp"):
        if extra.exists():
            candidates.append(extra)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
        if len(unique) >= MAX_TRACKED_FILES:
            break
    return unique


def bootstrap_state(state: dict[str, Any]) -> None:
    offsets: dict[str, int] = {}
    for path in candidate_logs():
        try:
            offsets[str(path.resolve())] = path.stat().st_size
        except FileNotFoundError:
            continue
    state["offsets"] = offsets
    state["bot_running"] = is_bababot_running()
    state["last_alert_at"] = utc_now()
    save_state(state)


def file_excerpt_for_marker(lines: list[str], marker_index: int) -> str:
    start = max(0, marker_index - 3)
    end = min(len(lines), marker_index + 60)
    error_header = re.compile(r"^\[?\d{4}-\d{2}-\d{2}.*(?:\[error|\berror\b)", re.IGNORECASE)
    for idx in range(marker_index + 1, end):
        if error_header.search(lines[idx]):
            end = idx
            break
    excerpt = "\n".join(lines[start:end]).strip()
    if len(excerpt) <= 1600:
        return excerpt
    # Keep both the error header/context and the final exception type; the middle of
    # long aiohttp/Discord tracebacks is less useful and previously hid the root cause.
    return excerpt[:650].rstrip() + "\n... traceback middle omitted ...\n" + excerpt[-900:].lstrip()


def canonical_marker_index(lowered_lines: list[str], marker_index: int) -> int:
    """Collapse an ERROR header and its following traceback into one incident."""
    line = lowered_lines[marker_index]
    if "traceback (most recent call last):" in line:
        for idx in range(marker_index - 1, max(-1, marker_index - 8), -1):
            candidate = lowered_lines[idx]
            if (
                "[error" in candidate
                or " error " in candidate
                or "interaction_failure" in candidate
                or "ignoring exception in view" in candidate
                or "ignoring exception in modal" in candidate
            ):
                return idx
    return marker_index


def is_ignored_error_excerpt(excerpt: str) -> bool:
    """Suppress user mistakes and transient Discord reconnect noise, never UI failures."""
    lowered = excerpt.lower()
    if any(signature in lowered for signature in TRANSIENT_INTERACTION_SIGNATURES):
        return True
    if any(signature in lowered for signature in INTERACTION_SIGNATURES):
        return False
    if any(signature in lowered for signature in IGNORED_COMMAND_SIGNATURES):
        return True
    monitor_task = "_send_monitor_message" in lowered or "monitorcog.monitor_checker" in lowered
    if monitor_task and any(signature in lowered for signature in MONITOR_DELIVERY_SIGNATURES):
        return True
    return any(signature in lowered for signature in RECONNECT_SIGNATURES)


def normalized_error_fingerprint(excerpt: str) -> str:
    """Ignore changing timestamps/incident IDs while retaining callback and custom_id context."""
    normalized = excerpt.lower()
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?z?\b", "<timestamp>", normalized)
    normalized = re.sub(r"\binc-\d{14}-[0-9a-f]{10}\b", "<incident>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def make_incident_id(source: Path, excerpt: str) -> str:
    digest = hashlib.sha256(f"{source.resolve()}::{excerpt}".encode("utf-8", "replace")).hexdigest()[:10]
    return f"inc-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{digest}"


def detect_error_events(text: str, source: Path, seen_hashes: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not text.strip():
        return events

    lines = text.splitlines()
    lowered = [line.lower() for line in lines]
    processed_roots: set[int] = set()
    for idx, line in enumerate(lowered):
        if not any(marker in line for marker in ERROR_MARKERS):
            continue
        root = canonical_marker_index(lowered, idx)
        if root in processed_roots:
            continue
        processed_roots.add(root)
        excerpt = file_excerpt_for_marker(lines, root)
        if not excerpt or is_ignored_error_excerpt(excerpt):
            continue
        fingerprint = normalized_error_fingerprint(excerpt)
        digest = hashlib.sha256(f"{source.resolve()}::{fingerprint}".encode("utf-8", "replace")).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        events.append(
            {
                "id": make_incident_id(source, excerpt),
                "hash": digest,
                "detected_at": utc_now(),
                "source": str(source.resolve()),
                "excerpt": excerpt,
                "status": "new",
            }
        )
    return events


def build_event(source: Path, excerpt: str, seen_hashes: set[str], *, kind: str = "runtime") -> dict[str, Any] | None:
    digest = hashlib.sha256(f"{source.resolve()}::{kind}::{excerpt}".encode("utf-8", "replace")).hexdigest()
    if digest in seen_hashes:
        return None
    seen_hashes.add(digest)
    return {
        "id": make_incident_id(source, excerpt),
        "hash": digest,
        "kind": kind,
        "detected_at": utc_now(),
        "source": str(source.resolve()),
        "excerpt": excerpt,
        "status": "new",
    }


def collect_health_events(running: bool, seen_hashes: set[str]) -> list[dict[str, Any]]:
    try:
        health = load_health_module()
        results = health.run_checks("live", monitor_max_age_seconds=600)
        # 確保 results 是 CheckResult 物件列表，如果 health check 傳回 None，處理它
        if results is None:
            issues = ["health checker returned None"]
        else:
            issues = [f"{result.name}: {result.detail}" for result in results if not result.ok]
    except Exception as exc:
        issues = [f"health checker failed: {exc.__class__.__name__}: {exc}"]

    if not issues:
        return []
    excerpt = "Bababot health check failed:\n" + "\n".join(f"- {issue}" for issue in issues)
    event = build_event(REPO_ROOT / "scripts" / "monitor_bababot.py", excerpt, seen_hashes, kind="health")
    return [] if event is None else [event]


def collect_new_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    offsets = state.setdefault("offsets", {})
    seen_hashes = set(state.get("seen_hashes", []))
    events: list[dict[str, Any]] = []

    running = is_bababot_running()
    previous_running = state.get("bot_running")
    if previous_running is True and not running:
        excerpt = "Bababot main.py process is no longer running. The bot may be offline."
        event = build_event(MAIN_PATH, excerpt, seen_hashes, kind="process")
        if event:
            events.append(event)
    state["bot_running"] = running
    events.extend(collect_health_events(running, seen_hashes))

    for path in candidate_logs():
        resolved = str(path.resolve())
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue

        old_offset = int(offsets.get(resolved, 0) or 0)
        if size < old_offset:
            old_offset = 0

        if size == old_offset:
            offsets[resolved] = size
            continue

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(old_offset)
            chunk = handle.read()
        offsets[resolved] = size
        events.extend(detect_error_events(chunk, path, seen_hashes))

    state["seen_hashes"] = list(seen_hashes)[-MAX_HASHES:]
    return events


def discord_api_request(url: str, token: str, payload: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
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


def get_dm_channel_id(state: dict[str, Any]) -> str:
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is not set")
    existing = state.get("dm_channel_id")
    if existing:
        return str(existing)
    dm = discord_api_request(
        "https://discord.com/api/v10/users/@me/channels",
        token,
        {"recipient_id": OWNER_ID},
    )
    channel_id = str(dm["id"])
    state["dm_channel_id"] = channel_id
    return channel_id


def send_discord_dm(state: dict[str, Any], message: str) -> None:
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is not set")
    channel_id = get_dm_channel_id(state)
    discord_api_request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        token,
        {"content": message},
    )


def format_alert(incident: dict[str, Any]) -> str:
    source_name = Path(incident["source"]).name
    excerpt = incident["excerpt"].strip()[:1200]
    return (
        f"[bababot watchdog] 偵測到新的 runtime error\n"
        f"incident: {incident['id']}\n"
        f"UTC: {incident['detected_at']}\n"
        f"source: {source_name}\n"
        f"status: waiting for owner confirmation\n\n"
        f"```\n{excerpt}\n```\n\n"
        f"回覆 `fix {incident['id']}` 讓 Hermes 自動修復並重啟 baba。\n"
        f"回覆 `skip {incident['id']}` 忽略這次事件。"
    )[:1900]


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch bababot logs for new errors and DM the owner.")
    parser.add_argument("--bootstrap", action="store_true", help="Record current log offsets and exit silently.")
    parser.add_argument("--dry-run", action="store_true", help="Print new alerts instead of sending them to Discord.")
    args = parser.parse_args()

    load_environment()
    state = load_state()

    if args.bootstrap:
        bootstrap_state(state)
        if args.dry_run:
            print(f"Bootstrapped watchdog state at {STATE_PATH}")
        return 0

    incidents = load_incidents()
    new_events = collect_new_events(state)
    if not new_events:
        save_state(state)
        return 0

    incidents.extend(new_events)
    for incident in new_events:
        if args.dry_run:
            print(format_alert(incident))
        else:
            try:
                send_discord_dm(state, format_alert(incident))
                incident["status"] = "notified"
                incident["notified_at"] = utc_now()
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                print(f"Discord API error: {exc.code} {body}")
                return 1
            except Exception as exc:
                print(f"Failed to send Discord DM: {exc}")
                return 1

    state["last_alert_at"] = utc_now()
    save_incidents(incidents)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
