from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = (REPO_ROOT / "main.py").resolve()
LOG_DIR = REPO_ROOT / "logs"
PID_FILE = LOG_DIR / "bababot.pid"
PROJECT_VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
STARTUP_WAIT_SECONDS = 6
SMOKE_TIMEOUT_SECONDS = 25
SUCCESS_MARKERS = (
    "has connected to gateway",
    "extensions loaded:",
    "slash commands synced:",
    "is now running!",
    "trpg loaded:",
)
FAILURE_MARKERS = (
    "traceback (most recent call last)",
    "[error",
    "failed to load extension",
    "modulenotfounderror",
    "extensionfailed",
    "trpg startup validation failed",
)
WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def _extract_windows_exe(command_line: str) -> str | None:
    command_line = command_line.strip()
    if not command_line:
        return None
    if command_line.startswith('"'):
        end = command_line.find('"', 1)
        return command_line[1:end] if end != -1 else None
    return command_line.split(" ", 1)[0]


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

    normalized_text = (
        result.stdout.replace("\r\r\n", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    processes: list[dict[str, Any]] = []
    pending_command_line: str | None = None
    for raw_line in normalized_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("CommandLine="):
            pending_command_line = line.split("=", 1)[1].strip()
            continue
        if not line.startswith("ProcessId=") or pending_command_line is None:
            continue
        pid_text = line.split("=", 1)[1].strip()
        try:
            pid = int(pid_text)
        except ValueError:
            pending_command_line = None
            continue
        processes.append(
            {
                "CommandLine": pending_command_line,
                "ProcessId": pid_text,
                "pid": pid,
                "command_line": pending_command_line,
            }
        )
        pending_command_line = None
    return processes


def find_bababot_processes() -> list[dict[str, Any]]:
    target = _normalize_path(str(MAIN_PATH))
    current_pid = os.getpid()
    matches: list[dict[str, Any]] = []
    for process in list_python_processes():
        command_line = process.get("command_line", "")
        if not command_line:
            continue
        normalized = _normalize_path(command_line)
        if target in normalized and process["pid"] != current_pid:
            matches.append(process)
    return matches


def terminate_process(pid: int) -> None:
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=WINDOWS_NO_WINDOW,
    )
    if result.returncode == 0:
        return

    output = f"{result.stdout}\n{result.stderr}".lower()
    if "not found" in output or "there is no running instance" in output:
        return

    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def choose_python_executable(existing_processes: list[dict[str, Any]]) -> str:
    if PROJECT_VENV_PYTHON.exists():
        return str(PROJECT_VENV_PYTHON)

    for process in existing_processes:
        exe = _extract_windows_exe(process.get("command_line", ""))
        if exe and Path(exe).exists():
            return exe
    return sys.executable


def wait_for_exit(pid: int, timeout_seconds: int = 20) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        live_pids = {process["pid"] for process in find_bababot_processes()}
        if pid not in live_pids:
            return True
        time.sleep(0.5)
    return False


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_watcher_command(log_path: Path, pid: int) -> list[str]:
    literal_path = _powershell_literal(str(log_path))
    script = (
        "$Host.UI.RawUI.WindowTitle = 'Bababot Live Log'; "
        f"$log = {literal_path}; $botPid = {pid}; "
        "Write-Host ('Watching Bababot PID ' + $botPid + ': ' + $log) -ForegroundColor Cyan; "
        "Write-Host 'Close this window to stop watching; Baba will keep running.'; "
        "Get-Content -LiteralPath $log -Tail 50; "
        "$stream = [System.IO.File]::Open($log, [System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite); "
        "$null = $stream.Seek(0, [System.IO.SeekOrigin]::End); "
        "$reader = [System.IO.StreamReader]::new($stream); "
        "try { while (Get-Process -Id $botPid -ErrorAction SilentlyContinue) { "
        "while (($line = $reader.ReadLine()) -ne $null) { Write-Host $line }; "
        "Start-Sleep -Milliseconds 250 } } "
        "finally { $reader.Dispose(); $stream.Dispose() }"
    )
    return [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


def start_log_watcher(log_path: Path, pid: int) -> subprocess.Popen[bytes] | None:
    if os.name != "nt":
        return None
    command = build_watcher_command(log_path, pid)
    return subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        close_fds=True,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def start_bababot(python_executable: str) -> tuple[subprocess.Popen[bytes], Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"bababot_restart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_handle = open(log_path, "a", encoding="utf-8")
    creationflags = 0
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONPATH", None)
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    # Use pythonw.exe instead of python.exe for subprocess to avoid console window
    pythonw_executable = Path(python_executable).parent / "pythonw.exe"
    executable_to_use = pythonw_executable if pythonw_executable.exists() else python_executable

    try:
        process = subprocess.Popen(
            [str(executable_to_use), "-u", str(MAIN_PATH)],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except Exception:
        log_handle.close()
        raise

    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    log_handle.close()
    start_log_watcher(log_path, process.pid)
    return process, log_path


def verify_started(pid: int) -> bool:
    time.sleep(STARTUP_WAIT_SECONDS)
    for process in find_bababot_processes():
        if process["pid"] == pid:
            return True
    return False


def read_log_text(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def run_smoke_check(pid: int, log_path: Path, timeout_seconds: int = SMOKE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_log_text = ""

    while time.time() < deadline:
        live_pids = {process["pid"] for process in find_bababot_processes()}
        last_log_text = read_log_text(log_path)
        lowered_log = last_log_text.lower()

        if pid not in live_pids:
            return False, "bababot process exited during smoke check"

        for marker in FAILURE_MARKERS:
            if marker in lowered_log:
                return False, f"startup log contains failure marker: {marker}"

        if all(marker in lowered_log for marker in SUCCESS_MARKERS):
            ext_match = re.search(r"extensions loaded:\s*(\d+)/(\d+)", lowered_log)
            if not ext_match:
                return False, "extension readiness marker has unexpected format"
            if int(ext_match.group(1)) != int(ext_match.group(2)):
                return False, f"extension readiness marker is not complete: {ext_match.group(0)}"

            sync_match = re.search(r"slash commands synced:\s*(\d+)", lowered_log)
            if not sync_match:
                return False, "slash-command readiness marker has unexpected format"

            match = re.search(
                r"trpg loaded:\s*(\d+) areas,\s*(\d+) monsters,\s*(\d+) items,\s*(\d+) skills",
                lowered_log,
            )
            if not match:
                return False, "TRPG readiness marker has unexpected format"
            if any(int(value) <= 0 for value in match.groups()):
                return False, f"TRPG readiness marker has zero count(s): {match.group(0)}"
            return True, f"process alive and startup markers observed; {ext_match.group(0)}; slash commands synced: {sync_match.group(1)}; {match.group(0)}"

        time.sleep(1)

    return False, "timed out waiting for startup success markers"


def main() -> int:
    if not MAIN_PATH.exists():
        print(f"main.py not found: {MAIN_PATH}", file=sys.stderr)
        return 1

    existing = find_bababot_processes()
    if existing:
        print("Found running bababot process(es):")
        for process in existing:
            print(f"- PID {process['pid']}: {process.get('command_line', '')}")
    else:
        print("No running bababot main.py process found.")

    python_executable = choose_python_executable(existing)
    print(f"Using Python executable: {python_executable}")

    for process in existing:
        pid = process["pid"]
        print(f"Stopping PID {pid}...")
        terminate_process(pid)
        if not wait_for_exit(pid):
            print(f"Timed out waiting for PID {pid} to exit.", file=sys.stderr)
            return 1

    print("Starting bababot...")
    new_process, log_path = start_bababot(python_executable)
    print(f"Started PID {new_process.pid}")
    print(f"Startup log: {log_path}")

    if verify_started(new_process.pid):
        smoke_ok, smoke_message = run_smoke_check(new_process.pid, log_path)
        if smoke_ok:
            print(f"Bababot restart succeeded. Smoke check passed: {smoke_message}.")
            return 0
        print(f"Smoke check failed: {smoke_message}.", file=sys.stderr)
    else:
        print("Bababot process did not stay alive long enough to verify startup.", file=sys.stderr)

    try:
        log_excerpt = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    except FileNotFoundError:
        log_excerpt = "<log file not found>"
    if log_excerpt:
        print("--- startup log tail ---", file=sys.stderr)
        print(log_excerpt, file=sys.stderr)
        print("--- end startup log tail ---", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
