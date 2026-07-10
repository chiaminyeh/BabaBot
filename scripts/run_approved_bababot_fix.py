from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
INCIDENTS_PATH = LOG_DIR / "watchdog_incidents.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def load_incidents() -> list[dict[str, Any]]:
    if not INCIDENTS_PATH.exists():
        return []
    try:
        return json.loads(INCIDENTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_incidents(incidents: list[dict[str, Any]]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    INCIDENTS_PATH.write_text(json.dumps(incidents, ensure_ascii=False, indent=2), encoding="utf-8")


def select_oldest_approved(incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    approved = [item for item in incidents if item.get("status") == "approved"]
    if not approved:
        return None
    return sorted(approved, key=lambda item: (item.get("approved_at") or item.get("detected_at") or "", item.get("id") or ""))[0]


def mark_status(incident_id: str, *, status: str, field_name: str) -> None:
    incidents = load_incidents()
    for item in incidents:
        if item.get("id") == incident_id:
            item["status"] = status
            item[field_name] = utc_now()
            break
    save_incidents(incidents)


def build_prompt(incident: dict[str, Any]) -> str:
    incident_json = json.dumps(incident, ensure_ascii=False, indent=2)
    return f"""You are operating inside the bababot repo at the current working directory.
Load and follow the bababot-dev skill if available.

A bababot watchdog incident was approved by the owner and should now be fixed.

Incident JSON:
{incident_json}

Required workflow:
1. Re-open logs/watchdog_incidents.json and confirm this incident still exists.
2. Investigate the referenced source/excerpt plus any current related files/logs to determine the real root cause.
3. Make the minimal code change needed to fix the issue.
4. Run the tightest relevant verification commands.
5. Restart bababot with `python scripts/restart_bababot.py` and require the smoke check to pass.
6. Update the same incident object in logs/watchdog_incidents.json:
   - On success: status='resolved', resolved_at=<UTC>, fix_summary=<short text>, verification_summary=<short text>, restart_log=<path if known>
   - On failure/blocker: status='blocked', blocked_at=<UTC>, blocker_summary=<short text>
7. In your final answer, report: incident id, root cause, files changed, verification results, restart result, and whether bababot is healthy.

Important constraints:
- Do not touch incidents other than this one.
- Do not say 'No approved incidents.'
- If the issue is already fixed by current code, still verify and restart before marking resolved.
- If you cannot complete the fix, mark blocked before finishing.
"""


def main() -> int:
    incidents = load_incidents()
    incident = select_oldest_approved(incidents)
    if not incident:
        return 0  # stay silent for cron no_agent mode

    incident_id = incident.get("id")
    if not incident_id:
        return 0

    mark_status(incident_id, status="in_progress", field_name="started_at")

    prompt = build_prompt(incident)
    command = [
        "hermes",
        "--yolo",
        "-s",
        "bababot-dev",
        "chat",
        "-q",
        prompt,
        "-t",
        "terminal,file,skills",
    ]
    result = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )

    output = (result.stdout or "").strip()
    error_output = (result.stderr or "").strip()

    if result.returncode != 0:
        incidents = load_incidents()
        for item in incidents:
            if item.get("id") == incident_id and item.get("status") == "in_progress":
                item["status"] = "blocked"
                item["blocked_at"] = utc_now()
                item["blocker_summary"] = f"Hermes fixer subprocess exited {result.returncode}: {(error_output or output)[:500]}"
                break
        save_incidents(incidents)
        message = output or error_output or f"Hermes fixer subprocess exited {result.returncode}."
        print(message)
        return 1

    if output:
        print(output)
    elif error_output:
        print(error_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
