from __future__ import annotations

import re
import unicodedata

TOUR_ALIASES = {
    "atp": "ATP",
    "wta": "WTA",
    "challenger": "CHALLENGER",
    "atp challenger": "CHALLENGER",
    "itf": "ITF",
    "exhibition": "EXHIBITION",
}


def fold_ascii(value: object) -> str:
    s = str(value or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s


def compact_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold_ascii(value).lower())


def normalize_player(value: object) -> str:
    text = re.sub(r"\s+", " ", fold_ascii(value)).strip()
    return text


def player_key(value: object) -> str:
    return compact_key(normalize_player(value))


def normalize_tour(value: object) -> str:
    text = re.sub(r"\s+", " ", fold_ascii(value).lower()).strip()
    if not text:
        return "UNKNOWN"
    return TOUR_ALIASES.get(text, text.upper())
