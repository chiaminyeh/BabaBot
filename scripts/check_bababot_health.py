from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
TRPG_DATA_DIR = REPO_ROOT / "trpg_data"
PLAYERS_DB_PATH = TRPG_DATA_DIR / "trpg_players.sqlite3"
WATCHDOG_STATE_PATH = LOG_DIR / "watchdog_state.json"
MAIN_PATH = (REPO_ROOT / "main.py").resolve()
EXPECTED_EXTENSIONS = [
    "music_cog",
    "chess_cog",
    "schedule_cog",
    "blackjack_cog",
    "poker_cog",
    "response_cog",
    "trpg_cog",
    "wordle_cog",
    "lottery_cog",
    "help_cog",
    "monitor_cog",
]
REQUIRED_TRPG_CONTENT = {
    "areas": "areas.json",
    "monsters": "monsters.json",
    "items": "items.json",
    "skills": "skills.json",
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(discord|bot|api|gemini|openai)[_-]?(token|key|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{20,}"),
    re.compile(r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}"),
]
IGNORE_DIRS = {".git", ".venv", "__pycache__", "logs", ".mypy_cache", ".pytest_cache"}
WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def list_python_processes() -> list[dict[str, Any]]:
    if os.name == "nt":
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe' or name='pythonw.exe'", "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            creationflags=WINDOWS_NO_WINDOW,
        )
        normalized = result.stdout.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
        processes: list[dict[str, Any]] = []
        current_command: str | None = None
        for raw_line in normalized.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("CommandLine="):
                current_command = line.split("=", 1)[1].strip()
                continue
            if line.startswith("ProcessId=") and current_command:
                try:
                    pid = int(line.split("=", 1)[1].strip())
                except ValueError:
                    current_command = None
                    continue
                processes.append({"pid": pid, "command_line": current_command})
                current_command = None
        return processes

    result = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=True)
    processes = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, command_line = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append({"pid": pid, "command_line": command_line})
    return processes


def is_bababot_running() -> bool:
    target = normalize_path(str(MAIN_PATH))
    current_pid = os.getpid()
    for process in list_python_processes():
        if process.get("pid") == current_pid:
            continue
        if target in normalize_path(process.get("command_line", "")):
            return True
    return False


def candidate_logs() -> list[Path]:
    if not LOG_DIR.exists():
        return []
    return sorted(LOG_DIR.glob("bababot_restart_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def latest_startup_log_text() -> tuple[Path | None, str]:
    logs = candidate_logs()
    if not logs:
        return None, ""
    path = logs[0]
    return path, path.read_text(encoding="utf-8", errors="replace")[-20000:]


def check_gateway_connected(running_required: bool) -> CheckResult:
    running = is_bababot_running()
    if running_required and not running:
        return CheckResult("gateway_connected", False, "bababot main.py process is not running")
    path, text = latest_startup_log_text()
    if not path:
        return CheckResult("gateway_connected", False, "no startup log found")
    ok = "has connected to gateway" in text.lower()
    return CheckResult("gateway_connected", ok, f"{path.name}: {'gateway marker present' if ok else 'gateway marker missing'}")


def check_extensions_loaded() -> CheckResult:
    path, text = latest_startup_log_text()
    lowered = text.lower()
    if not path:
        return CheckResult("extensions_loaded", False, "no startup log found")
    if "failed to load extension" in lowered or "extensionfailed" in lowered:
        return CheckResult("extensions_loaded", False, f"{path.name}: extension failure marker present")
    match = re.search(r"extensions loaded:\s*(\d+)/(\d+)\s*\(([^)]*)\)", text, flags=re.I)
    if not match:
        return CheckResult("extensions_loaded", False, f"{path.name}: Extensions loaded marker missing")
    loaded, expected = int(match.group(1)), int(match.group(2))
    ok = loaded == expected == len(EXPECTED_EXTENSIONS)
    return CheckResult("extensions_loaded", ok, f"{path.name}: {loaded}/{expected} loaded")


def check_trpg_content_loaded() -> CheckResult:
    path, text = latest_startup_log_text()
    if path and text:
        match = re.search(r"TRPG loaded:\s*(\d+) areas,\s*(\d+) monsters,\s*(\d+) items,\s*(\d+) skills", text, flags=re.I)
        if match:
            counts = tuple(int(v) for v in match.groups())
            ok = all(v > 0 for v in counts)
            return CheckResult("trpg_content_loaded", ok, f"{path.name}: areas={counts[0]} monsters={counts[1]} items={counts[2]} skills={counts[3]}")

    issues = []
    for label, filename in REQUIRED_TRPG_CONTENT.items():
        file_path = TRPG_DATA_DIR / filename
        if not file_path.exists():
            issues.append(f"{filename} missing")
            continue
        data = json.loads(file_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not data:
            issues.append(f"{filename} has 0 {label}")
    return CheckResult("trpg_content_loaded", not issues, "static content OK" if not issues else "; ".join(issues))


def check_sqlite_writable() -> CheckResult:
    if not PLAYERS_DB_PATH.exists():
        return CheckResult("sqlite_writable", False, f"{PLAYERS_DB_PATH.name} missing")
    try:
        with sqlite3.connect(PLAYERS_DB_PATH) as conn:
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                return CheckResult("sqlite_writable", False, "integrity_check failed: " + "; ".join(map(str, integrity[:5])))
            conn.execute("CREATE TABLE IF NOT EXISTS healthcheck_probe (id INTEGER PRIMARY KEY CHECK (id = 1), checked_at TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO healthcheck_probe (id, checked_at) VALUES (1, datetime('now'))")
            conn.execute("DELETE FROM healthcheck_probe WHERE id = 1")
            conn.commit()
    except Exception as exc:
        return CheckResult("sqlite_writable", False, f"{exc.__class__.__name__}: {exc}")
    return CheckResult("sqlite_writable", True, "integrity_check ok; write probe ok")


def check_slash_commands_synced() -> CheckResult:
    path, text = latest_startup_log_text()
    if not path:
        return CheckResult("slash_commands_synced", False, "no startup log found")
    match = re.search(r"slash commands synced:\s*(\d+)", text, flags=re.I)
    if not match:
        match = re.search(r"synced\s+(\d+)\s+command", text, flags=re.I)
    if not match:
        return CheckResult("slash_commands_synced", False, f"{path.name}: slash sync marker missing")
    count = int(match.group(1))
    return CheckResult("slash_commands_synced", count >= 0, f"{path.name}: {count} command(s) synced")


def check_no_unhandled_traceback() -> CheckResult:
    path, text = latest_startup_log_text()
    if not path:
        return CheckResult("no_unhandled_traceback", False, "no startup log found")
    lowered = text.lower()
    markers = ["traceback (most recent call last)", "[error", "failed to load extension", "modulenotfounderror", "extensionfailed"]
    hits = [marker for marker in markers if marker in lowered]
    return CheckResult("no_unhandled_traceback", not hits, "no failure markers" if not hits else "markers: " + ", ".join(hits))


def check_monitor_running(max_age_seconds: int) -> CheckResult:
    if not WATCHDOG_STATE_PATH.exists():
        return CheckResult("monitor_running", False, "watchdog_state.json missing; run scripts/monitor_bababot.py once or enable cron")
    age = time.time() - WATCHDOG_STATE_PATH.stat().st_mtime
    ok = age <= max_age_seconds
    return CheckResult("monitor_running", ok, f"watchdog_state.json age={age:.0f}s threshold={max_age_seconds}s")


def iter_project_text_files():
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fname in files:
            path = Path(root) / fname
            if path.suffix.lower() not in {".py", ".json", ".yml", ".yaml", ".md", ".txt"}:
                continue
            yield path


def check_secrets() -> CheckResult:
    hits: list[str] = []
    for path in iter_project_text_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in {".env", "bank.json", "daily.json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(rel)
                break
    return CheckResult("secret_scan", not hits, "no obvious secrets" if not hits else "possible secrets in: " + ", ".join(hits[:10]))


def run_checks(mode: str, monitor_max_age_seconds: int) -> list[CheckResult]:
    checks = [
        check_trpg_content_loaded(),
        check_secrets(),
    ]
    if mode == "live":
        checks.extend([
            check_gateway_connected(running_required=True),
            check_extensions_loaded(),
            check_sqlite_writable(),
            check_slash_commands_synced(),
            check_no_unhandled_traceback(),
            check_monitor_running(monitor_max_age_seconds),
        ])
    elif mode == "ci":
        checks.append(CheckResult("live_service_checks", True, "skipped in CI mode"))
    else:
        raise ValueError(f"unknown mode: {mode}")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Check bababot service health and CI-safe content gates.")
    parser.add_argument("--mode", choices=["live", "ci"], default="live")
    parser.add_argument("--monitor-max-age-seconds", type=int, default=600)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = run_checks(args.mode, args.monitor_max_age_seconds)
    if args.json:
        print(json.dumps([result.__dict__ for result in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            status = "OK" if result.ok else "FAIL"
            print(f"[{status}] {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
