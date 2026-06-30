"""Lightweight i18n helpers: t() for code-side messages, tf() for JSON game-data fields."""

import json
import os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trpg_data")
_CATALOG_PATH = os.path.join(_DATA_DIR, "locale_en.json")
_catalog = {}


def _load():
    global _catalog
    if os.path.exists(_CATALOG_PATH):
        with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
            _catalog = json.load(f)
    else:
        _catalog = {}


_load()


def reload_catalog():
    _load()


def t(lang: str, key: str, fallback_zh: str, **kwargs) -> str:
    template = fallback_zh
    if lang == "en":
        template = _catalog.get(key) or fallback_zh
    return template.format(**kwargs) if kwargs else template


def tf(obj: dict, field: str, lang: str):
    if lang == "en":
        en_val = obj.get(f"{field}_en")
        if en_val:
            return en_val
    return obj.get(field)
