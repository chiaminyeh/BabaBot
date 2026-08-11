#!/usr/bin/env python
"""Small always-on-top AI token usage monitor for local desktop use.

The tool intentionally reads only local usage/log artifacts and skips files whose
names look like credentials. Most AI desktop/CLI tools do not expose one common
usage API, so each provider is configurable with glob patterns. Use
``config/ai_token_monitor.example.json`` as a starting point, copy it to
``config/ai_token_monitor.json``, then add/remove paths for the tools you use.

Run once for verification:
    python scripts/ai_token_monitor.py --once

Run the floating window:
    python scripts/ai_token_monitor.py
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover - --once works without a display/tk
    tk = None
    ttk = None

APP_NAME = "AI Token Monitor"
DEFAULT_CONFIG = Path("config/ai_token_monitor.json")
EXAMPLE_CONFIG = Path("config/ai_token_monitor.example.json")
TOKEN_KEY_RE = re.compile(
    r"(?:^|_)(?:input|output|prompt|completion|cache(?:d)?|reasoning|total)?_?tokens?$|"
    r"(?:token_count|tokens_(?:in|out|used)|usage_tokens)",
    re.IGNORECASE,
)
TEXT_TOKEN_RE = re.compile(
    r"(?P<label>input|output|prompt|completion|cache(?:d)?|reasoning|total)?\s*"
    r"tokens?\s*[:=]\s*(?P<value>\d{1,12})",
    re.IGNORECASE,
)
SKIP_NAME_RE = re.compile(
    r"(?:auth|secret|credential|apikey|api_key|token\.json|keychain|cookies?)",
    re.IGNORECASE,
)
DEFAULT_MAX_FILE_MB = 25
DEFAULT_REFRESH_SECONDS = 30

DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "Codex",
        "patterns": [
            "~/.codex/**/*.jsonl",
            "~/.codex/**/*.sqlite",
            "~/.codex/**/*.db",
            "~/.codex/**/*.log",
        ],
    },
    {
        "name": "Antigravity",
        "patterns": [
            "~/.antigravity/**/*",
            "~/.config/antigravity/**/*",
            "~/AppData/Roaming/Antigravity/**/*",
            "~/AppData/Local/Antigravity/**/*",
        ],
    },
    {
        "name": "Google AI",
        "patterns": [
            "~/.gemini/**/*",
            "~/.config/gemini/**/*",
            "~/AppData/Roaming/Google/Gemini/**/*",
            "~/AppData/Local/Google/Gemini/**/*",
            "~/AppData/Roaming/google-generative-ai/**/*",
        ],
    },
    {
        "name": "Copilot",
        "patterns": [
            "~/AppData/Roaming/Code/User/globalStorage/github.copilot*/**/*",
            "~/AppData/Roaming/Code - Insiders/User/globalStorage/github.copilot*/**/*",
            "~/.config/github-copilot/**/*",
        ],
    },
    {
        "name": "Nous/Hermes",
        "patterns": [
            "~/.hermes/**/*.jsonl",
            "~/.hermes/**/*.sqlite",
            "~/.hermes/**/*.db",
            "~/AppData/Local/hermes/**/*.jsonl",
            "~/AppData/Local/hermes/**/*.sqlite",
            "~/AppData/Local/hermes/**/*.db",
            "~/AppData/Roaming/Hermes/**/*",
            "~/AppData/Local/Nous/**/*",
        ],
    },
    {
        "name": "Manus AI",
        "patterns": [
            "~/.manus/**/*",
            "~/AppData/Roaming/Manus/**/*",
            "~/AppData/Local/Manus/**/*",
        ],
    },
]


@dataclass
class Usage:
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    files_scanned: int = 0
    files_matched: int = 0
    errors: list[str] = field(default_factory=list)

    def add_token_value(self, key: str, value: int) -> None:
        key_l = key.lower()
        if "cache" in key_l:
            self.cached_tokens += value
        elif "reason" in key_l:
            self.reasoning_tokens += value
        elif key_l in {"input", "input_tokens", "prompt", "prompt_tokens", "tokens_in"} or "prompt" in key_l:
            self.input_tokens += value
        elif key_l in {"output", "output_tokens", "completion", "completion_tokens", "tokens_out"} or "completion" in key_l:
            self.output_tokens += value
        elif "total" in key_l or key_l in {"tokens", "token_count", "usage_tokens", "tokens_used"}:
            self.total_tokens += value
        else:
            self.total_tokens += value

    @property
    def computed_total(self) -> int:
        # Cached/reasoning counters are useful detail, but vendor totals often
        # already include them. Do not add cached tokens on top of input tokens,
        # otherwise tools such as Codex can look almost 2x larger than their own
        # explicit total.
        subtotal = self.input_tokens + self.output_tokens
        return max(self.total_tokens, subtotal)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.computed_total,
            "explicit_total_tokens": self.total_tokens,
            "files_matched": self.files_matched,
            "files_scanned": self.files_scanned,
            "errors": self.errors[:5],
        }


def expand_path(pattern: str) -> str:
    return os.path.expandvars(os.path.expanduser(pattern))


def is_safe_to_scan(path: Path) -> bool:
    text = str(path).replace("\\", "/")
    return not SKIP_NAME_RE.search(text)


def iter_candidate_files(patterns: Iterable[str]) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in patterns:
        expanded = expand_path(pattern)
        matches = glob.glob(expanded, recursive=True)
        for match in matches:
            p = Path(match)
            if p.is_dir() or not p.exists() or not is_safe_to_scan(p):
                continue
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(p)
    return files


def add_tokens_from_obj(obj: Any, usage: Usage) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if TOKEN_KEY_RE.search(str(key)):
                numeric_value = parse_int(value)
                if numeric_value is not None:
                    usage.add_token_value(str(key), numeric_value)
            elif isinstance(value, (dict, list)):
                add_tokens_from_obj(value, usage)
    elif isinstance(obj, list):
        for item in obj:
            add_tokens_from_obj(item, usage)


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


def scan_jsonish(path: Path, usage: Usage) -> None:
    suffix = path.suffix.lower()
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        if suffix == ".jsonl":
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    add_tokens_from_obj(json.loads(line), usage)
                except json.JSONDecodeError:
                    scan_text_line(line, usage)
        else:
            text = fh.read()
            try:
                add_tokens_from_obj(json.loads(text), usage)
            except json.JSONDecodeError:
                for line in text.splitlines():
                    scan_text_line(line, usage)


def scan_text_line(line: str, usage: Usage) -> None:
    for match in TEXT_TOKEN_RE.finditer(line):
        label = match.group("label") or "total"
        usage.add_token_value(label, int(match.group("value")))


def scan_text(path: Path, usage: Usage) -> None:
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            scan_text_line(line, usage)


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")]


def scan_sqlite(path: Path, usage: Usage) -> None:
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        for table in sqlite_tables(conn):
            try:
                cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                token_cols = [str(col[1]) for col in cols if TOKEN_KEY_RE.search(str(col[1]))]
                if not token_cols:
                    continue
                quoted = ", ".join(f'SUM(COALESCE("{col}", 0))' for col in token_cols)
                row = conn.execute(f'SELECT {quoted} FROM "{table}"').fetchone()
                if row:
                    for col, value in zip(token_cols, row):
                        if isinstance(value, (int, float)):
                            usage.add_token_value(col, int(value))
            except sqlite3.DatabaseError as exc:
                usage.errors.append(f"{path.name}:{table}: {exc}")
    finally:
        conn.close()


def scan_file(path: Path, usage: Usage, max_file_mb: int) -> None:
    try:
        if path.stat().st_size > max_file_mb * 1024 * 1024:
            usage.errors.append(f"skipped large file: {path}")
            return
        suffix = path.suffix.lower()
        if suffix in {".sqlite", ".db", ".sqlite3"}:
            scan_sqlite(path, usage)
        elif suffix in {".json", ".jsonl", ".ndjson"}:
            scan_jsonish(path, usage)
        elif suffix in {".log", ".txt", ".csv"}:
            if suffix == ".csv":
                scan_csv(path, usage)
            else:
                scan_text(path, usage)
    except Exception as exc:  # keep one bad vendor file from killing the monitor
        usage.errors.append(f"{path}: {exc}")


def scan_csv(path: Path, usage: Usage) -> None:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            add_tokens_from_obj(row, usage)


def load_config(path: Path) -> dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "refresh_seconds": DEFAULT_REFRESH_SECONDS,
        "max_file_mb": DEFAULT_MAX_FILE_MB,
        "providers": DEFAULT_PROVIDERS,
    }


def scan_all(config: dict[str, Any]) -> dict[str, Any]:
    max_file_mb = int(config.get("max_file_mb", DEFAULT_MAX_FILE_MB))
    results: list[dict[str, Any]] = []
    for provider in config.get("providers", DEFAULT_PROVIDERS):
        name = str(provider.get("name", "Unknown"))
        usage = Usage(provider=name)
        files = iter_candidate_files(provider.get("patterns", []))
        usage.files_matched = len(files)
        for path in files:
            before = usage.computed_total
            scan_file(path, usage, max_file_mb)
            if usage.computed_total != before or path.suffix.lower() in {".sqlite", ".db", ".sqlite3", ".json", ".jsonl", ".ndjson", ".log", ".txt", ".csv"}:
                usage.files_scanned += 1
        results.append(usage.as_dict())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": results,
        "grand_total_tokens": sum(item["total_tokens"] for item in results),
    }


class MonitorWindow:
    def __init__(self, config_path: Path) -> None:
        if tk is None or ttk is None:
            raise RuntimeError("tkinter is not available; use --once for JSON output")
        self.config_path = config_path
        self.config = load_config(config_path)
        self.refresh_ms = max(5, int(self.config.get("refresh_seconds", DEFAULT_REFRESH_SECONDS))) * 1000
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("560x320")
        self.root.attributes("-topmost", True)
        self.topmost = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Starting...")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        ttk.Checkbutton(toolbar, text="置頂", variable=self.topmost, command=self.apply_topmost).pack(side="left")
        ttk.Button(toolbar, text="立即刷新", command=self.refresh).pack(side="left", padx=8)
        ttk.Label(toolbar, textvariable=self.status).pack(side="right")

        columns = ("provider", "total", "input", "output", "cached", "reasoning", "files")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", height=9)
        headings = {
            "provider": "Provider",
            "total": "Total",
            "input": "Input",
            "output": "Output",
            "cached": "Cached",
            "reasoning": "Reasoning",
            "files": "Files",
        }
        widths = {"provider": 120, "total": 90, "input": 80, "output": 80, "cached": 80, "reasoning": 80, "files": 55}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="e" if col != "provider" else "w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        note = ttk.Label(
            self.root,
            text="提示：複製 config/ai_token_monitor.example.json 到 config/ai_token_monitor.json 可自訂各 AI 工具的紀錄路徑。",
            wraplength=530,
            justify="left",
        )
        note.pack(fill="x", padx=8, pady=(0, 8))

    def apply_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost.get()))

    def refresh(self) -> None:
        self.config = load_config(self.config_path)
        data = scan_all(self.config)
        for item in self.tree.get_children():
            self.tree.delete(item)
        for provider in data["providers"]:
            self.tree.insert(
                "",
                "end",
                values=(
                    provider["provider"],
                    f'{provider["total_tokens"]:,}',
                    f'{provider["input_tokens"]:,}',
                    f'{provider["output_tokens"]:,}',
                    f'{provider["cached_tokens"]:,}',
                    f'{provider["reasoning_tokens"]:,}',
                    f'{provider["files_scanned"]}/{provider["files_matched"]}',
                ),
            )
        self.status.set(f"Total {data['grand_total_tokens']:,} · {time.strftime('%H:%M:%S')}")
        self.apply_topmost()
        self.root.after(self.refresh_ms, self.refresh)

    def run(self) -> None:
        self.root.mainloop()


def ensure_example_config() -> None:
    if EXAMPLE_CONFIG.exists():
        return
    EXAMPLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    EXAMPLE_CONFIG.write_text(
        json.dumps(
            {
                "refresh_seconds": DEFAULT_REFRESH_SECONDS,
                "max_file_mb": DEFAULT_MAX_FILE_MB,
                "providers": DEFAULT_PROVIDERS,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Floating AI token usage monitor")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="JSON config path")
    parser.add_argument("--once", action="store_true", help="print one JSON scan and exit")
    parser.add_argument("--write-example", action="store_true", help="write example config and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    ensure_example_config()
    if args.write_example:
        print(EXAMPLE_CONFIG)
        return 0
    if args.once:
        print(json.dumps(scan_all(load_config(args.config)), ensure_ascii=False, indent=2))
        return 0
    MonitorWindow(args.config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
