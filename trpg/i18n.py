"""Lightweight i18n helpers: t() for code-side messages, tf() for JSON game-data fields."""

import json
import os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trpg_data")
_CATALOG_PATH_EN = os.path.join(_DATA_DIR, "locale_en.json")
_CATALOG_PATH_ZH = os.path.join(_DATA_DIR, "locale_zh.json")
_catalog_en = {}
_catalog_zh = {}


def _load():
    global _catalog_en, _catalog_zh
    if os.path.exists(_CATALOG_PATH_EN):
        with open(_CATALOG_PATH_EN, "r", encoding="utf-8") as f:
            _catalog_en = json.load(f)
    else:
        _catalog_en = {}
    if os.path.exists(_CATALOG_PATH_ZH):
        with open(_CATALOG_PATH_ZH, "r", encoding="utf-8") as f:
            _catalog_zh = json.load(f)
    else:
        _catalog_zh = {}


_load()


def reload_catalog():
    _load()


def t(lang: str, key: str, fallback_zh: str, **kwargs) -> str:
    template = fallback_zh
    if lang == "en":
        template = _catalog_en.get(key) or fallback_zh
    elif lang == "zh":
        template = _catalog_zh.get(key) or fallback_zh
    return template.format(**kwargs) if kwargs else template


def tf(obj: dict, field: str, lang: str):
    if lang == "en":
        en_val = obj.get(f"{field}_en")
        if en_val:
            return en_val
    return obj.get(field)
