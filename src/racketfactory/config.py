from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ROUTES_PATH = ROOT / "config" / "routes.json"


def load_routes(path: str | Path = ROUTES_PATH) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    return data if isinstance(data, dict) else {}
