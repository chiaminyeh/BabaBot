from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_repo_script_module(name: str, filename: str):
    script_path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = _load_repo_script_module("watch_bababot_errors", "watch_bababot_errors.py")
restart = _load_repo_script_module("restart_bababot", "restart_bababot.py")

REPO_ROOT = Path(__file__).resolve().parents[1]
INCIDENTS_PATH = REPO_ROOT / "logs" / "watchdog_incidents.json"
AUTO_FIX_SIGNATURES = (
    "Bababot main.py process is no longer running.",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def load_incidents() -> list[dict[str, Any]]:
    return watch.load_incidents()


def save_incidents(incidents: list[dict[str, Any]]) -> None:
    watch.save_incidents(incidents)


def incident_by_id(incidents: list[dict[str, Any]], incident_id: str) -> dict[str, Any] | None:
    for item in incidents:
        if item.get("id") == incident_id:
            return item
    return None


def update_incident(incident_id: str, **fields: Any) -> dict[str, Any] | None:
    incidents = load_incidents()
    incident = incident_by_id(incidents, incident_id)
    if incident is None:
        return None
    incident.update(fields)
    save_incidents(incidents)
    return incident


def is_auto_fixable(incident: dict[str, Any]) -> bool:
    excerpt = incident.get("excerpt", "")
    return any(signature in excerpt for signature in AUTO_FIX_SIGNATURES)


def run_fix_for_incident(incident: dict[str, Any]) -> tuple[int, str, str]:
    incident_id = incident.get("id")
    if not incident_id:
        return 1, "", "incident missing id"

    update_incident(
        incident_id,
        status="in_progress",
        approved_at=utc_now(),
        started_at=utc_now(),
        approval_message_id="auto-monitor",
        auto_fix_reason="matched simple runtime signature",
    )

    try:
        exit_code = restart.main()
    except Exception as exc:
        return 1, "", f"fixed restart remediation failed: {exc.__class__.__name__}: {exc}"

    if exit_code == 0:
        update_incident(
            incident_id,
            status="resolved",
            resolved_at=utc_now(),
            fix_summary="Fixed remediation: restarted bababot main.py and restart smoke check passed.",
        )
        return 0, "fixed restart remediation succeeded", ""

    return exit_code, "", f"fixed restart remediation exited {exit_code}"


def mark_blocked(incident_id: str, summary: str) -> None:
    update_incident(
        incident_id,
        status="blocked",
        blocked_at=utc_now(),
        blocker_summary=summary[:1000],
    )


def format_major_alert(incident: dict[str, Any]) -> str:
    excerpt = (incident.get("excerpt") or "").strip()[:1200]
    source = Path(incident.get("source") or "unknown").name
    return (
        f"[bababot monitor] 偵測到需要你看的錯誤\n"
        f"incident: {incident.get('id')}\n"
        f"source: {source}\n"
        f"UTC: {incident.get('detected_at')}\n\n"
        f"```\n{excerpt}\n```"
    )[:1900]


def format_blocked_alert(incident: dict[str, Any]) -> str:
    source = Path(incident.get("source") or "unknown").name
    blocker = (incident.get("blocker_summary") or "auto-fix failed").strip()[:1000]
    excerpt = (incident.get("excerpt") or "").strip()[:700]
    return (
        f"[bababot monitor] 簡單錯誤自動修復失敗，需要你看一下\n"
        f"incident: {incident.get('id')}\n"
        f"source: {source}\n"
        f"UTC: {incident.get('detected_at')}\n"
        f"blocker: {blocker}\n\n"
        f"```\n{excerpt}\n```"
    )[:1900]


def run_monitor() -> tuple[int, list[str]]:
    watch.load_environment()
    state = watch.load_state()
    incidents = load_incidents()
    new_events = watch.collect_new_events(state)
    pending_incidents = [incident for incident in incidents if incident.get("status") == "new"]

    if not new_events and not pending_incidents:
        watch.save_state(state)
        return 0, []

    if new_events:
        incidents.extend(new_events)
        save_incidents(incidents)
        pending_incidents.extend(new_events)

    messages: list[str] = []
    for incident in pending_incidents:
        incident_id = incident.get("id")
        if not incident_id:
            continue

        if not is_auto_fixable(incident):
            update_incident(incident_id, status="reported", reported_at=utc_now())
            messages.append(format_major_alert(incident))
            continue

        code, stdout, stderr = run_fix_for_incident(incident)
        refreshed = incident_by_id(load_incidents(), incident_id) or incident
        if code != 0:
            mark_blocked(incident_id, stdout or stderr or f"Hermes fixer subprocess exited {code}.")
            refreshed = incident_by_id(load_incidents(), incident_id) or refreshed
            messages.append(format_blocked_alert(refreshed))
            continue

        final_status = refreshed.get("status")
        if final_status != "resolved":
            if final_status != "blocked":
                mark_blocked(incident_id, stdout or stderr or "Auto-fix finished without marking the incident resolved.")
                refreshed = incident_by_id(load_incidents(), incident_id) or refreshed
            messages.append(format_blocked_alert(refreshed))

    watch.save_state(state)
    return 0, messages


def main() -> int:
    code, messages = run_monitor()
    if messages:
        print("\n\n".join(messages))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
