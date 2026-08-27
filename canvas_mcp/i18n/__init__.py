"""Message catalogues.

Everything a user of this server actually reads — tool descriptions, error
messages, relative-time labels — lives here rather than inline, so the server can
speak a language other than English.

`en` is the default and the only catalogue shipped in the repository. Additional
catalogues are dropped in as `<lang>.py` exporting a `MESSAGES` dict; a missing or
broken catalogue silently falls back to English, so a partial translation can
never break the server.

Select one with `"lang": "<code>"` in config.json, or `CANVAS_MCP_LANG`.
"""
from __future__ import annotations

import importlib
from typing import Any

from . import en

FALLBACK = en.MESSAGES
_cache: dict[str, dict[str, Any]] = {"en": FALLBACK}


def _catalogue() -> dict[str, Any]:
    # Imported lazily: config imports this module for its own error strings, so a
    # top-level import would be circular.
    from .. import config

    lang = (config.lang() or "en").lower()
    if lang in _cache:
        return _cache[lang]
    try:
        mod = importlib.import_module(f".{lang}", __package__)
        catalogue = dict(FALLBACK)
        catalogue.update(mod.MESSAGES)
    except Exception:  # noqa: BLE001 - unknown or broken catalogue -> English
        catalogue = FALLBACK
    _cache[lang] = catalogue
    return catalogue


def raw(key: str) -> Any:
    """Look up an entry without formatting. Use for non-string entries such as
    the weekday list."""
    return _catalogue().get(key, FALLBACK.get(key, key))


def t(key: str, **kw: Any) -> str:
    """Look up a message and interpolate it.

    A placeholder missing from the catalogue returns the template unformatted
    rather than raising — a bad translation should degrade, not crash.
    """
    template = raw(key)
    if not isinstance(template, str):
        return str(template)
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        return template
