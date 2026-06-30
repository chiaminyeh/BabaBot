"""Maintenance check: run this after adding new game content or new code messages
to see exactly what's missing English translations.

  python check_translations.py

Checks:
  1. Every translatable field in trpg_data/*.json (name/desc/title/etc.) has a
     non-empty "_en" sibling.
  2. Every t(lang, "key", ...) call site in the .py files has a matching entry
     in trpg_data/locale_en.json.
"""
import json
import os
import re

DATA_DIR = "trpg_data"
SKIP_FILES = {"trpg_players.json", "equipment.json", "locale_en.json", "glossary.json"}
TRANSLATABLE_FIELDS = {
    "name", "title", "desc", "description", "label", "npc_name",
    "message", "area_name", "transform_text", "name_suffix",
    "result_text", "accept_prompt", "turn_in_prompt",
}

CALL_RE = re.compile(r"""\bt\(\s*[\w.\[\]'"]+\s*,\s*["']([\w.]+)["']""")


def scan_json_missing(node, path, missing):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in TRANSLATABLE_FIELDS and isinstance(v, str) and v != "":
                en_val = node.get(f"{k}_en")
                if not en_val:
                    missing.append(f"{path}/{k}")
            scan_json_missing(v, f"{path}/{k}", missing)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            scan_json_missing(item, f"{path}[{i}]", missing)


def check_json():
    by_file = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json") or fname in SKIP_FILES:
            continue
        data = json.load(open(os.path.join(DATA_DIR, fname), encoding="utf-8"))
        missing = []
        scan_json_missing(data, fname, missing)
        if missing:
            by_file[fname] = missing

    if not by_file:
        print("[JSON data] All translatable fields have English translations.")
        return

    total = sum(len(v) for v in by_file.values())
    print(f"[JSON data] {total} missing/empty _en field(s):")
    for fname, missing in by_file.items():
        print(f"  {fname}: {len(missing)} missing")
        for m in missing[:10]:
            print(f"    - {m}")
        if len(missing) > 10:
            print(f"    ... and {len(missing) - 10} more")


def check_py_keys():
    catalog = json.load(open(os.path.join(DATA_DIR, "locale_en.json"), encoding="utf-8"))
    used_keys = {}
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "scripts")]
        for fname in files:
            if fname.endswith(".py") and fname != "check_translations.py":
                path = os.path.join(root, fname)
                text = open(path, encoding="utf-8").read()
                for key in CALL_RE.findall(text):
                    used_keys.setdefault(key, path)

    missing = sorted(k for k in used_keys if k not in catalog)
    if not missing:
        print(f"[Code strings] All {len(used_keys)} t() keys have English catalog entries.")
        return

    by_prefix = {}
    for k in missing:
        prefix = k.split(".")[0]
        by_prefix.setdefault(prefix, []).append(k)

    print(f"[Code strings] {len(missing)} t() key(s) used but missing from locale_en.json:")
    for prefix, keys in sorted(by_prefix.items()):
        print(f"  {prefix}.*: {len(keys)} missing")
        for k in keys[:10]:
            print(f"    - {k}  (in {used_keys[k]})")
        if len(keys) > 10:
            print(f"    ... and {len(keys) - 10} more")


if __name__ == "__main__":
    check_json()
    print()
    check_py_keys()
