"""One-off scaffolding pass: ensure every translatable field in trpg_data/*.json
has an "_en" sibling (null placeholder if not yet translated). Run once before
dispatching translation work; safe to re-run later when new content is added."""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "trpg_data")
SKIP_FILES = {"trpg_players.json", "equipment.json", "locale_en.json", "glossary.json"}
TRANSLATABLE_FIELDS = {
    "name", "title", "desc", "description", "label", "npc_name",
    "message", "area_name", "transform_text", "name_suffix",
    "result_text", "accept_prompt", "turn_in_prompt",
}


def inject_en_siblings(node):
    added = 0
    if isinstance(node, dict):
        for key in list(node.keys()):
            if key in TRANSLATABLE_FIELDS and isinstance(node[key], str) and f"{key}_en" not in node:
                node[f"{key}_en"] = None
                added += 1
        for v in node.values():
            added += inject_en_siblings(v)
    elif isinstance(node, list):
        for item in node:
            added += inject_en_siblings(item)
    return added


def main():
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json") or fname in SKIP_FILES:
            continue
        path = os.path.join(DATA_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        added = inject_en_siblings(data)
        if added:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"{fname}: added {added} _en field(s)")
        else:
            print(f"{fname}: already complete")


if __name__ == "__main__":
    main()
