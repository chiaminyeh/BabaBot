"""Merge all per-agent locale_en.<area>.json scratch catalogs into the shared
trpg_data/locale_en.json, then delete the scratch files. Reports any key
collisions (same key, different value) across scratch files before merging."""
import json
import os
import glob

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "trpg_data")
MAIN_PATH = os.path.join(DATA_DIR, "locale_en.json")


def main():
    main_catalog = json.load(open(MAIN_PATH, "r", encoding="utf-8"))
    scratch_files = sorted(glob.glob(os.path.join(DATA_DIR, "locale_en.*.json")))

    collisions = []
    merged_from = []
    for path in scratch_files:
        fname = os.path.basename(path)
        data = json.load(open(path, "r", encoding="utf-8"))
        for k, v in data.items():
            if k in main_catalog and main_catalog[k] != v:
                collisions.append(f"{fname}: key {k!r} = {v!r} conflicts with existing {main_catalog[k]!r}")
            else:
                main_catalog[k] = v
        merged_from.append((fname, len(data)))

    if collisions:
        print("COLLISIONS FOUND (not merged, fix manually):")
        for c in collisions:
            print("  " + c)
        return

    with open(MAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(main_catalog, f, ensure_ascii=False, indent=4, sort_keys=True)

    for path in scratch_files:
        os.remove(path)

    print(f"Merged {len(merged_from)} scratch file(s) into locale_en.json (total {len(main_catalog)} keys):")
    for fname, count in merged_from:
        print(f"  {fname}: {count} keys")


if __name__ == "__main__":
    main()
