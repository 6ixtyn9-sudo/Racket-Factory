#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.assay import odds_band, score_rows
from racketfactory.warehouse import DEFAULT_DB_PATH, connect


def main() -> int:
    con = connect(DEFAULT_DB_PATH)
    try:
        rows = con.execute("SELECT * FROM market_sides").fetch_df().to_dict("records")
    finally:
        con.close()

    groups: dict[str, list[dict]] = defaultdict(list)
    groups["overall"] = rows
    for row in rows:
        fav_key = "favorite" if row.get("is_favorite") else "underdog"
        band = odds_band(row.get("decimal_odds"))
        groups[fav_key].append(row)
        groups[f"tour={row.get('tour') or 'UNKNOWN'}"].append(row)
        groups[f"tournament={row.get('tournament') or 'UNKNOWN'}"].append(row)
        groups[f"odds={band}"].append(row)
        groups[f"{fav_key}|odds={band}"].append(row)
        groups[f"tour={row.get('tour') or 'UNKNOWN'}|{fav_key}|odds={band}"].append(row)

    report = {key: score_rows(value) for key, value in sorted(groups.items())}
    out = ROOT / "localdata" / "market_audit.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"market audit -> {out}")
    for key, summary in report.items():
        if key in {"overall", "favorite", "underdog"} or key.startswith("favorite|odds=") or key.startswith("underdog|odds="):
            print(f"  {key}: n={summary['n']} hit={summary['hit_rate']} roi={summary['roi']} avg_odds={summary['avg_odds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
