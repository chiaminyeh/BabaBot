"""Maintenance check: run this after adding new game content or new code messages
to see exactly what's missing English translations.

  python check_translations.py

Checks:
  1. Every translatable field in trpg_data/*.json (name/desc/title/etc.) has a
     non-empty "_en" sibling.
  2. Every zh/en JSON pair uses the same placeholder names ({foo}, {bar}, ...).
  3. Every t(lang, "key", ...) call site in the .py files has a matching entry
     in trpg_data/locale_en.json.
  4. discord.ui.Label(text=...)/Modal(title=...) strings stay under Discord's
     hard component-length limits (45 chars) in BOTH zh and en — translated
     strings routinely run 2-3x longer than the Chinese original, and a call
     that only checks the zh fallback (which is what code review sees) can
     look perfectly safe while the en catalog entry silently exceeds the
     limit. Discord doesn't truncate: it rejects the entire modal (400
     Invalid Form Body), so the button just does nothing for English players.
     See git history: modal.stat_point_qty_label was exactly this bug.
"""
import ast
import json
import os
import re
import sys

DATA_DIR = "trpg_data"
SKIP_FILES = {"trpg_players.json", "equipment.json", "locale_en.json", "glossary.json"}
TRANSLATABLE_FIELDS = {
    "name", "title", "desc", "description", "label", "npc_name",
    "message", "area_name", "transform_text", "name_suffix",
    "result_text", "accept_prompt", "turn_in_prompt", "intro", "identity",
}

CALL_RE = re.compile(r"""\bt\(\s*[\w.\[\]'\"]+\s*,\s*[\"']([\w.]+)[\"']""")
PLACEHOLDER_RE = re.compile(r"{([^{}]+)}")


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



def check_json_placeholders(fname, node, path=""):
    issues = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and not k.endswith("_en"):
                en_key = f"{k}_en"
                en_val = node.get(en_key)
                if isinstance(en_val, str):
                    zh_fields = sorted(set(PLACEHOLDER_RE.findall(v)))
                    en_fields = sorted(set(PLACEHOLDER_RE.findall(en_val)))
                    if zh_fields != en_fields:
                        issues.append((f"{fname}{path}/{k}", zh_fields, en_fields))
            issues.extend(check_json_placeholders(fname, v, f"{path}/{k}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            issues.extend(check_json_placeholders(fname, item, f"{path}[{i}]"))
    return issues



def check_json():
    by_file = {}
    placeholder_issues = []
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json") or fname in SKIP_FILES:
            continue
        data = json.load(open(os.path.join(DATA_DIR, fname), encoding="utf-8"))
        missing = []
        scan_json_missing(data, fname, missing)
        if missing:
            by_file[fname] = missing
        placeholder_issues.extend(check_json_placeholders(fname, data))

    issues = 0

    if not by_file:
        print("[JSON data] All translatable fields have English translations.")
    else:
        issues += sum(len(v) for v in by_file.values())
        total = sum(len(v) for v in by_file.values())
        print(f"[JSON data] {total} missing/empty _en field(s):")
        for fname, missing in by_file.items():
            print(f"  {fname}: {len(missing)} missing")
            for m in missing[:10]:
                print(f"    - {m}")
            if len(missing) > 10:
                print(f"    ... and {len(missing) - 10} more")

    if not placeholder_issues:
        print("[JSON placeholders] All zh/en field pairs use matching placeholder names.")
    else:
        issues += len(placeholder_issues)
        print(f"[JSON placeholders] {len(placeholder_issues)} zh/en pair(s) disagree on placeholder names:")
        for path, zh_fields, en_fields in placeholder_issues[:20]:
            print(f"  - {path} | zh={zh_fields} | en={en_fields}")
        if len(placeholder_issues) > 20:
            print(f"    ... and {len(placeholder_issues) - 20} more")

    return issues



def check_py_keys():
    catalog = json.load(open(os.path.join(DATA_DIR, "locale_en.json"), encoding="utf-8"))
    used_keys = {}
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "scripts", ".venv")]
        for fname in files:
            if fname.endswith(".py") and fname != "check_translations.py":
                path = os.path.join(root, fname)
                text = open(path, encoding="utf-8").read()
                for key in CALL_RE.findall(text):
                    used_keys.setdefault(key, path)

    missing = sorted(k for k in used_keys if k not in catalog)
    if not missing:
        print(f"[Code strings] All {len(used_keys)} t() keys have English catalog entries.")
        return 0

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
    return len(missing)



def _t_call_key_and_fallback(node):
    """If node is a call to t(lang, "key", "zh fallback", ...), return (key, fallback)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t"):
        return None
    if len(node.args) < 3:
        return None
    key_node, fallback_node = node.args[1], node.args[2]
    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str) and \
       isinstance(fallback_node, ast.Constant) and isinstance(fallback_node.value, str):
        return key_node.value, fallback_node.value
    return None



def check_component_limits():
    catalog = json.load(open(os.path.join(DATA_DIR, "locale_en.json"), encoding="utf-8"))
    # (field label, Discord's hard limit)
    LABEL_FIELDS = {"text": 45, "description": 100}
    problems = []

    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "scripts", ".venv")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                # discord.ui.Label(text=..., description=...)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Label":
                    for kw in node.keywords:
                        if kw.arg not in LABEL_FIELDS:
                            continue
                        hit = _t_call_key_and_fallback(kw.value)
                        if not hit:
                            continue
                        key, zh = hit
                        limit = LABEL_FIELDS[kw.arg]
                        en = catalog.get(key, zh)
                        if len(zh) > limit or len(en) > limit:
                            problems.append((path, node.lineno, f"Label.{kw.arg}", key, limit, len(zh), len(en)))

                # discord.ui.Modal subclass: super().__init__(title=t(...)) and class-level title="..."
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "__init__":
                    is_super_call = isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Name) and node.func.value.func.id == "super"
                    if not is_super_call:
                        continue
                    for kw in node.keywords:
                        if kw.arg != "title":
                            continue
                        hit = _t_call_key_and_fallback(kw.value)
                        if not hit:
                            continue
                        key, zh = hit
                        en = catalog.get(key, zh)
                        if len(zh) > 45 or len(en) > 45:
                            problems.append((path, node.lineno, "Modal.title", key, 45, len(zh), len(en)))

    if not problems:
        print("[Component limits] All Modal titles / Label text-description stay within Discord's length limits.")
        return 0

    print(f"[Component limits] {len(problems)} string(s) risk exceeding Discord's hard limit (raw template, before placeholder substitution — actual runtime length can only be longer):")
    for path, lineno, field, key, limit, zh_len, en_len in problems:
        print(f"  {path}:{lineno}  {field} (limit {limit})  key={key}  zh={zh_len} chars, en={en_len} chars")
    return len(problems)


if __name__ == "__main__":
    issues = 0
    issues += check_json()
    print()
    issues += check_py_keys()
    print()
    issues += check_component_limits()
    sys.exit(1 if issues else 0)
