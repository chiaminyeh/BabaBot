from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

COMMANDS = [
    [sys.executable, "-m", "compileall", "-q", "."],
    [sys.executable, "-m", "unittest", "discover", "tests"],
    [sys.executable, "check_translations.py"],
    [sys.executable, "scripts/check_bababot_health.py", "--mode", "ci"],
    [sys.executable, "scripts/validate_json_references.py"],
    ["git", "diff", "--check"],
]


def main() -> int:
    for command in COMMANDS:
        print("\n$ " + " ".join(command))
        result = subprocess.run(command, cwd=REPO_ROOT)
        if result.returncode != 0:
            print(f"pre-commit gate failed: {' '.join(command)}", file=sys.stderr)
            return result.returncode
    print("\npre-commit gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
