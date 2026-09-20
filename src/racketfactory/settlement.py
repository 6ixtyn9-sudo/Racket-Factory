"""Shared settlement primitives for audit + grading.

Single source of truth for identity matching, result-row finality, candidate
ranking and conflict detection. Both ``scripts/audit_recent_picks.py`` and
``scripts/auto_tickets_grade.py`` must use this module so the published audit
and the bankroll can never disagree about what a result means.

Design notes (from Sept-11/12 2026 evidence):
  * Compound surnames (Alcala Gurri, Diaz Acosta, Carreno Busta, De Jong) must
    match across full-name / surname+initial / particle spellings, but a bare
    first surname token ("Alcala") must NOT match a longer compound
    ("Alcala Gurri M.") — Nicolas Alcala is a different player.
  * Bare surname <-> surname+initial is allowed (doubles picks are commonly
    surname-only, e.g. "Cascino / Feng" vs TE "Cascino E / Feng S.") and is
    flagged via ``bare_surname=True`` so callers can disclose it.
  * Bare surname <-> full given+surname is rejected (the Zverev rule:
    "Zverev" must never settle against "Alexander Zverev" or "Mischa Zverev").
  * Result rows are validated for finality before they may settle anything:
    incomplete final sets (``5-4``), live markers and winner/score incoherence
    force PENDING, never a loss.
  * Walkovers (winner but no sets played) are VOID (stake returned), never
    wins. Retirements settle to the advancer and are flagged RETIRED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from racketfactory.entities import player_key

_WS_SPLIT = re.compile(r"\s+")
_SEED_STRIP = re.compile(r"\s*\(\d+\)\s*")
_STATUS_RET = re.compile(r"\b(ret\.?|retired|retirement|abd\.?|abandoned|def\.?|defaulted)\b", re.IGNORECASE)
_STATUS_WO = re.compile(r"\b(w\.?o\.?|walkover)\b", re.IGNORECASE)
_LIVE_MARKERS = (
    " live", "live:", "s1", "s2", "s3", "s4", "s5",
    "in progress", "in-play", "inplay", " to finish", "suspended",
    "interrupted", "postponed", "cancelled",
)
_SET_PREFIX = re.compile(r"^(\d+)\s*-\s*(\d+)\s+(.*)$")
_SET_TOKEN = re.compile(r"^(\d+)\s*[-:]\s*(\d+)(?:\(\d+\))?$")
_NAN_STRINGS = {"", "nan", "none", "<na>", "nat", "null", "n/a"}


def clean_str(value: object) -> str:
    """Coerce CSV/pandas artifacts (NaN, 'nan', None) to clean strings."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    text = str(value).strip()
    return "" if text.lower() in _NAN_STRINGS else text


def _tokens(name: object) -> list[str]:
    text = str(name or "").strip()
    text = _SEED_STRIP.sub(" ", text)
    # Split hyphenated compounds ("Carreno-Busta", "Cavalle-Reimers") into
    # separate tokens so they match space-separated spellings.
    text = text.replace("-", " ").replace("–", " ").replace("/", " ")
    out: list[str] = []
    for tok in _WS_SPLIT.split(text):
        key = player_key(tok)
        if key:
            out.append(key)
    return out


@dataclass
class ParsedPlayer:
    raw: str
    core: list[str] = field(default_factory=list)
    initials: list[str] = field(default_factory=list)

    @property
    def bare(self) -> bool:
        return len(self.core) == 1 and not self.initials


def parse_player(name: object) -> ParsedPlayer:
    toks = _tokens(name)
    initials: list[str] = []
    while toks and len(toks[-1]) == 1:
        initials.insert(0, toks.pop())
    return ParsedPlayer(raw=str(name or ""), core=toks, initials=initials)


def _tok_eq(a: str, b: str) -> bool:
    """Token compatibility: equal, initial-prefix (``p`` ~ ``pablo``), or
    truncation-prefix (``kulambaye`` ~ ``kulambayeva``).

    The challenger backfill truncates names to ~9 display characters
    (``Montgomer``, ``Papamicha``, ``Brancacci``), so a long token that is a
    strict prefix of the other (length gap <= 2, shorter >= 7 chars) counts
    as compatible. Known residual risk: same-family masculine/feminine pairs
    (``Kovalev``/``Kovaleva``) also satisfy this; the pair+date scope of
    settlement plus conflict detection make a wrong merge unlikely, and a
    wrong merge there surfaces as a conflict, never a silent win.
    """
    if a == b:
        return True
    if len(a) == 1 and len(b) > 1:
        return b.startswith(a)
    if len(b) == 1 and len(a) > 1:
        return a.startswith(b)
    if len(a) >= 7 and len(b) >= 7 and abs(len(a) - len(b)) <= 2:
        return a.startswith(b) or b.startswith(a)
    return False


def _contiguous_subseq(short: list[str], long: list[str]) -> int:
    """Start index of ``short`` inside ``long`` (token-compatible), or -1."""
    if not short or len(short) > len(long):
        return -1
    for i in range(len(long) - len(short) + 1):
        if all(_tok_eq(s, x) for s, x in zip(short, long[i:])):
            return i
    return -1


def players_match(a: object, b: object) -> tuple[bool, bool]:
    """Return ``(match, bare_surname_leniency_used)``.

    Leniency is used only when one side is a bare surname and the other side
    carries nothing but the same surname (+ optional initial).
    """
    pa, pb = parse_player(a), parse_player(b)
    if not pa.core or not pb.core:
        return False, False
    if pa.core == pb.core:
        if pa.initials and pb.initials and pa.initials != pb.initials:
            return False, False
        bare_used = (pa.bare or pb.bare) and not (pa.initials and pb.initials)
        return True, bool(bare_used)
    if len(pa.core) <= len(pb.core):
        short, long = pa, pb
    else:
        short, long = pb, pa
    start = _contiguous_subseq(short.core, long.core)
    if pa.initials and pb.initials and pa.initials != pb.initials:
        return False, False
    if start < 0:
        # Surname-anchored fallback: the short side's full given name plus
        # surname ("Irene Burillo") against a compound-surname row
        # ("Burillo Escorihuela I.") never aligns as a contiguous span.
        # Anchor on the surname; given tokens must each match a remaining
        # long token or the long side's initials; leftover long tokens are
        # allowed only when a full given name agreed (initial or token).
        j = -1
        for k, tok in enumerate(long.core):
            if _tok_eq(short.core[-1], tok):
                j = k
                break
        if j < 0:
            return False, False
        used = {j}
        full_given_hit = False
        initial_given_hit = False
        for g in short.core[:-1]:
            hit = False
            for k, tok in enumerate(long.core):
                if k in used:
                    continue
                if _tok_eq(g, tok):
                    used.add(k)
                    hit = True
                    if len(g) > 1:
                        full_given_hit = True
                    break
            if not hit:
                if len(g) > 1 and g[0] in set(long.initials):
                    initial_given_hit = True
                elif not (len(g) == 1 and g in set(long.initials)):
                    return False, False
        given_ok = full_given_hit or (
            initial_given_hit
            and any(len(g) > 1 for g in short.core[:-1]))
        for k, tok in enumerate(long.core):
            if k in used:
                continue
            if len(tok) == 1:
                if short.initials and tok not in set(short.initials):
                    return False, False
                continue
            if not given_ok:
                return False, False
        return True, False

    pre = long.core[:start]
    post = long.core[start + len(short.core):]
    short_given = set(short.initials)

    def _extra_ok(tok: str, position: int, is_pre: bool) -> bool:
        if len(tok) == 1:
            # Initial-like extra: allowed unless it conflicts with the
            # other side's initials.
            return not (short.initials and tok not in short_given)
        if tok[0] in short_given:
            return True
        if is_pre and position > 0:
            # Middle given names ("Genaro ALBERTO Olivieri" vs "Olivieri
            # G.") are unverifiable against a single initial but cannot
            # contradict it once the first given matched.
            return True
        # Compound fully shared (2+ core tokens) with no given-info on the
        # short side: "Alcala Gurri" vs "Max Alcala Gurri" is the same
        # person (PredixSport-style vs full). Bare single surnames ("Zverev")
        # stay ambiguous and still reject. Post-span (surname-side) extras
        # always reject: "Alcala" vs "Alcala Gurri" is ambiguous.
        if is_pre and len(short.core) >= 2 and not short.initials:
            return True
        return False

    for i, tok in enumerate(pre):
        if not _extra_ok(tok, i, True):
            return False, False
    for i, tok in enumerate(post):
        if not _extra_ok(tok, i, False):
            return False, False
    bare_used = short.bare and not pre and not post and not short.initials
    return True, bool(bare_used)


_TEAM_SEP = re.compile(r"\s*(?:/|&|\band\b|,)\s*", re.IGNORECASE)


def split_team(name: object) -> list[str]:
    """Split a team into member display names (``/``, ``&``, ``and``, ``,``).

    No source in this pipeline uses ``Last, First`` singles order, so a
    comma always separates doubles partners here.
    """
    text = str(name or "").strip()
    parts = [p.strip() for p in _TEAM_SEP.split(text) if p.strip()]
    return parts or [""]


_MATCH_SPLIT = re.compile(r"\s+vs\.?\s+|\s+v\s+|\s+–\s+|\s+-\s+(?=[A-Z])")


def parse_match_teams(match_text: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ``"A / B vs C / D"`` into two teams of player display names."""
    text = str(match_text or "").strip()
    parts = _MATCH_SPLIT.split(text, maxsplit=1)
    if len(parts) != 2:
        return (), ()
    teams: list[tuple[str, ...]] = []
    for part in parts:
        members = tuple(p.strip() for p in part.split("/") if p.strip())
        teams.append(members)
    return teams[0], teams[1]


def teams_match(pick_match: object, result_a: object, result_b: object) -> tuple[bool, bool]:
    """Order-insensitive team comparison. Returns (match, bare_used)."""
    pa, pb = parse_match_teams(pick_match)
    if not pa or not pb:
        return False, False
    if len(pa) != len(pb):
        return False, False
    ra = tuple(p.strip() for p in str(result_a or "").split("/") if p.strip())
    rb = tuple(p.strip() for p in str(result_b or "").split("/") if p.strip())
    if len(ra) != len(pa) or len(rb) != len(pb):
        return False, False

    def _side(px: tuple[str, ...], rx: tuple[str, ...]) -> tuple[bool, bool]:
        if len(px) == 1:
            return players_match(px[0], rx[0])
        # Doubles: order-insensitive set match (partner swaps must fail).
        if len(px) != 2 or len(rx) != 2:
            return False, False
        m1, b1 = players_match(px[0], rx[0])
        m2, b2 = players_match(px[1], rx[1])
        if m1 and m2:
            return True, b1 or b2
        m3, b3 = players_match(px[0], rx[1])
        m4, b4 = players_match(px[1], rx[0])
        if m3 and m4:
            return True, b3 or b4
        return False, False

    straight_a, ba1 = _side(pa, ra)
    straight_b, ba2 = _side(pb, rb)
    if straight_a and straight_b:
        return True, ba1 or ba2
    swap_a, bb1 = _side(pa, rb)
    swap_b, bb2 = _side(pb, ra)
    if swap_a and swap_b:
        return True, bb1 or bb2
    return False, False


def home_is_player_a(pred_home: object, pred_away: object,
                     player_a: object, player_b: object) -> bool | None:
    """Orientation of a home/away prediction onto player_a/player_b.

    Strict and unambiguous: exactly one of the straight/swapped assignments
    may match (initials included, so Zverev-vs-Zverev rows resolve instead
    of colliding). Returns None when ambiguous or unmapped.
    """
    ha = players_match(pred_home, player_a)[0]
    hb = players_match(pred_home, player_b)[0]
    ah = players_match(pred_away, player_a)[0]
    ab = players_match(pred_away, player_b)[0]
    straight = ha and ab
    swapped = hb and ah
    if straight and not swapped:
        return True
    if swapped and not straight:
        return False
    return None


def selection_won(selection: object, winner_a: object, winner_b: object,
                  result_winner: object) -> bool | None:
    """Did ``selection`` (a team from the pick match) win? None if unclear."""
    sel = tuple(p.strip() for p in str(selection or "").split("/") if p.strip())
    if not sel:
        return None
    w = str(result_winner or "").strip()
    if not w:
        return None
    ra = tuple(p.strip() for p in str(winner_a or "").split("/") if p.strip())
    rb = tuple(p.strip() for p in str(winner_b or "").split("/") if p.strip())
    if len(sel) == 1 and len(ra) == 1 and len(rb) == 1:
        ma, _ = players_match(sel[0], ra[0])
        mb, _ = players_match(sel[0], rb[0])
        mw, _ = players_match(sel[0], w)
        if mw:
            return True
        if (ma and not mb) or (mb and not ma):
            # Selection matches one side and the winner matches neither side
            # explicitly: decide by side.
            wa, _ = players_match(w, ra[0])
            wb, _ = players_match(w, rb[0])
            if ma and wb and not wa:
                return False
            if mb and wa and not wb:
                return False
        return None
    # Doubles: selection must equal the winning team (order-insensitive).
    wteam = tuple(p.strip() for p in w.split("/") if p.strip())
    if len(sel) != 2 or len(wteam) != 2:
        return None
    opts = (
        (players_match(sel[0], wteam[0])[0] and players_match(sel[1], wteam[1])[0]),
        (players_match(sel[0], wteam[1])[0] and players_match(sel[1], wteam[0])[0]),
    )
    if any(opts):
        return True
    # Selection equals the losing team?
    for side in (ra, rb):
        if len(side) == 2:
            if ((players_match(sel[0], side[0])[0] and players_match(sel[1], side[1])[0])
                    or (players_match(sel[0], side[1])[0] and players_match(sel[1], side[0])[0])):
                mw, _ = players_match(side[0], wteam[0])
                if not mw:
                    return False
    return None


@dataclass
class ScoreCheck:
    sets_a: int | None = None
    sets_b: int | None = None
    valid: bool = False
    reason: str = ""
    completed_sets: int = 0


def _set_games_ok(g1: int, g2: int, *, deciding: bool) -> bool:
    hi, lo = max(g1, g2), min(g1, g2)
    if hi < 6:
        return False
    if hi == 6 and lo <= 4:
        return True
    if hi == 7 and lo in (5, 6):
        return True
    if hi > 7 and hi - lo >= 2:
        return True
    if deciding and hi >= 10 and hi - lo >= 2:
        return True
    return False


def check_score(score: object) -> ScoreCheck:
    """Validate a result score string such as ``"2-1 6-4 4-6 10-8"``."""
    text = clean_str(score)
    if not text:
        return ScoreCheck(valid=False, reason="empty score")
    m = _SET_PREFIX.match(text)
    prefix: tuple[int, int] | None = None
    rest = text
    if m and int(m.group(1)) <= 3 and int(m.group(2)) <= 3:
        # Leading "N-M" is a set-count prefix only for plausible set counts;
        # otherwise it is the first set ("6-4 6-4" has no prefix).
        prefix = (int(m.group(1)), int(m.group(2)))
        rest = m.group(3).strip()
    if not rest:
        return ScoreCheck(sets_a=prefix[0] if prefix else None,
                          sets_b=prefix[1] if prefix else None,
                          valid=False, reason="set counts without set scores")

    def _split_fused(value: int, other: int) -> tuple[int, int]:
        """Split fused tiebreak digits (``7-68`` -> 7-6 with tb 8).

        TennisExplorer renders the loser's tiebreak points inside the games
        cell (``61`` = 6 games + 1 point). Only 6/7-leading values facing a
        6/7 are fused; real extended scores (``10-8``) are untouched.
        """
        text_value = str(value)
        if (value >= 60 and other in (6, 7)
                and text_value[0] in ("6", "7") and len(text_value) >= 2):
            return int(text_value[0]), int(text_value[1:])
        return value, -1

    sets: list[tuple[int, int]] = []
    for tok in rest.split():
        sm = _SET_TOKEN.match(tok)
        if not sm:
            return ScoreCheck(valid=False, reason=f"unparseable set token {tok!r}")
        g1, g2 = int(sm.group(1)), int(sm.group(2))
        if g2 >= 60 and g1 in (6, 7):
            g2, _ = _split_fused(g2, g1)
        elif g1 >= 60 and g2 in (6, 7):
            g1, _ = _split_fused(g1, g2)
        sets.append((g1, g2))
    if not sets:
        return ScoreCheck(valid=False, reason="no set scores")
    won_a = won_b = 0
    for i, (g1, g2) in enumerate(sets):
        deciding = i == len(sets) - 1 and len(sets) >= 3
        last = i == len(sets) - 1
        if not _set_games_ok(g1, g2, deciding=deciding or len(sets) == 1):
            if last:
                return ScoreCheck(valid=False,
                                  reason=f"incomplete final set {g1}-{g2}")
            return ScoreCheck(valid=False,
                              reason=f"invalid set {g1}-{g2}")
        if g1 > g2:
            won_a += 1
        else:
            won_b += 1
    if prefix and (prefix[0] != won_a or prefix[1] != won_b):
        return ScoreCheck(valid=False, reason=(
            f"set-count prefix {prefix[0]}-{prefix[1]} disagrees with "
            f"set scores ({won_a}-{won_b})"))
    return ScoreCheck(sets_a=won_a, sets_b=won_b, valid=True,
                      completed_sets=len(sets), reason="ok")


def sets_only_pair(row: dict[str, Any]) -> tuple[int, int] | None:
    """Parse ``_sets_a``/``_sets_b`` evidence, or None when absent."""
    try:
        sets_a = row.get("_sets_a")
        sets_b = row.get("_sets_b")
        if (sets_a is None or sets_b is None
                or str(sets_a).strip() in ("", "nan", "None")
                or str(sets_b).strip() in ("", "nan", "None")):
            return None
        return int(float(sets_a)), int(float(sets_b))
    except (TypeError, ValueError):
        return None


@dataclass
class Finality:
    final: bool
    reason: str
    status: str = ""  # "", RETIRED, WALKOVER, ABANDONED


def row_finality(row: dict[str, Any]) -> Finality:
    """Decide whether a result row is safe to settle from. Never settle live,
    incomplete, or incoherent rows."""
    winner = clean_str(row.get("winner"))
    score = clean_str(row.get("score"))
    blob = " ".join(clean_str(row.get(k)) for k in
                    ("score", "winner", "_comment", "_result_status",
                     "round", "tournament")).lower()
    if "abandoned" in blob or "cancelled" in blob or "canceled" in blob:
        return Finality(True, "match abandoned/cancelled", status="CANCELLED")
    if any(mark in blob for mark in _LIVE_MARKERS):
        return Finality(False, "live/in-progress markers present")
    if not winner:
        return Finality(False, "no winner recorded")
    if _STATUS_WO.search(blob):
        if not score or check_score(score).completed_sets == 0:
            return Finality(True, "walkover: no sets played", status="WALKOVER")
    if _STATUS_RET.search(blob) or "retired" in str(row.get("_comment") or "").lower():
        return Finality(True, "retirement: advancer recorded", status="RETIRED")
    sets_pair = sets_only_pair(row)
    if not score and sets_pair is not None:
        # Scores-feed rows (completed events): sets won without per-set
        # strings. The feed marks events completed; that is final, provided
        # the winner owns the set majority.
        if sets_pair[0] != sets_pair[1]:
            leader = row.get("player_a") if sets_pair[0] > sets_pair[1] else row.get("player_b")
            if not players_match(winner, leader)[0]:
                return Finality(False, "winner disagrees with set majority")
        return Finality(True, "winner with completed sets from scores feed")
    chk = check_score(score)
    if sets_pair is not None and chk.valid:
        if sets_pair != (chk.sets_a, chk.sets_b):
            return Finality(False, "S column disagrees with set scores")
    if not chk.valid:
        if not score:
            # Winner without any score: accept only explicit walkover/bye
            # evidence, otherwise the row is too weak to settle.
            return Finality(False, "winner without score evidence")
        return Finality(False, f"score not final: {chk.reason}")
    if chk.sets_a != chk.sets_b:
        # The recorded winner must own the majority of sets (catches
        # cascade rows whose winner and scoreline describe different
        # matches). Ties (retirement-shortened) skip this check.
        leader = row.get("player_a") if chk.sets_a > chk.sets_b else row.get("player_b")  # type: ignore[operator]
        if not players_match(winner, leader)[0]:
            return Finality(False, "winner disagrees with set majority")
    return Finality(True, "winner with coherent completed score")


def _row_date(row: dict[str, Any]) -> str:
    return clean_str(row.get("match_date"))[:10]


def _same_winner_side(w1: str, w2: str) -> bool:
    """Team-aware winner equivalence.

    "Ben Shelton" and "Shelton B." are the same winner; exact-string
    grouping would flag them as a conflict. Doubles winners compare
    member-by-member in a canonical order.
    """
    t1 = split_team(w1)
    t2 = split_team(w2)
    if len(t1) == 2 or len(t2) == 2:
        if len(t1) != 2 or len(t2) != 2:
            return False
        a1 = sorted(t1, key=player_key)
        a2 = sorted(t2, key=player_key)
        return all(players_match(x, y)[0] for x, y in zip(a1, a2))
    return players_match(w1, w2)[0]


def rank_candidates(rows: list[dict[str, Any]], target_date: str) -> list[tuple[int, dict[str, Any], Finality]]:
    """Rank candidate result rows. Higher score first.

    Exact date beats adjacent; rows with ids/tournaments/scores beat weak
    legacy rows. Callers use this for both settlement and conflict detection.
    """
    ranked: list[tuple[int, dict[str, Any], Finality]] = []
    for row in rows:
        fin = row_finality(row)
        score = 0
        if _row_date(row) == target_date:
            score += 4
        if clean_str(row.get("_te_id")) or clean_str(row.get("_result_id")):
            score += 2
        if clean_str(row.get("tournament")):
            score += 1
        if clean_str(row.get("score")):
            score += 1
        if str(row.get("_row_quality") or "") == "legacy":
            score -= 1
        ranked.append((score, row, fin))
    ranked.sort(key=lambda t: t[0], reverse=True)
    return ranked


@dataclass
class Settlement:
    outcome: str  # WON / LOST / PENDING / VOID / CONFLICT
    reason: str
    basis: dict[str, Any] = field(default_factory=dict)
    bare_surname: bool = False


def settle_selection(pick_match: object, selection: object,
                     candidates: list[dict[str, Any]],
                     target_date: str) -> Settlement:
    """Settle one selection against candidate result rows.

    ``candidates`` may be unfiltered (typically all result rows within ±1 day
    of the pick); team matching is applied here so callers cannot skip it.
    Handles date ranking, finality gating and cross-row conflicts.
    """
    matched: list[dict[str, Any]] = []
    bare_used = False
    for row in candidates:
        ok, bare = teams_match(pick_match, row.get("player_a"),
                               row.get("player_b"))
        if ok:
            matched.append(row)
            bare_used = bare_used or bare
    if not matched:
        return Settlement("PENDING", "no matching result rows found")
    candidates = matched
    ranked = rank_candidates(candidates, target_date)
    finals = [(s, r) for (s, r, f) in ranked if f.final]
    if not finals:
        (_, _, weak) = ranked[0]
        return Settlement("PENDING", f"result not final: {weak.reason}",
                          basis={"match_date": _row_date(ranked[0][1]),
                                 "score": clean_str(ranked[0][1].get("score"))})
    # Conflict: final rows naming winners that are not the same side.
    # Spelling variants ("Ben Shelton" vs "Shelton B.") cluster together.
    groups: list[dict[str, Any]] = []
    for _, row in finals:
        w = clean_str(row.get("winner"))
        for rep in groups:
            if _same_winner_side(w, clean_str(rep.get("winner"))):
                break
        else:
            groups.append(row)
    if len(groups) > 1:
        detail = "; ".join(
            f"{clean_str(r.get('source')) or '?'}@{_row_date(r) or '?'}:"
            f"{clean_str(r.get('winner')) or '?'}"
            f"({clean_str(r.get('score')) or 'sets-only'})"
            for r in groups)
        return Settlement("CONFLICT",
                          f"conflicting final results: {detail}")
    best = finals[0][1]
    won = selection_won(selection, best.get("player_a"), best.get("player_b"),
                        best.get("winner"))
    fin = row_finality(best)
    basis = {
        "source": clean_str(best.get("source")),
        "match_date": _row_date(best),
        "score": clean_str(best.get("score")),
        "winner": clean_str(best.get("winner")),
        "result_id": clean_str(best.get("_te_id")) or clean_str(best.get("_result_id")),
        "tournament": clean_str(best.get("tournament")),
        "status": fin.status,
    }
    if fin.status in ("WALKOVER", "CANCELLED", "ABANDONED"):
        return Settlement("VOID", f"{fin.status.lower()}: stake returned", basis=basis,
                          bare_surname=bare_used)
    if won is True:
        return Settlement("WON", f"settled from {basis['source']} "
                          f"{basis['match_date']} {basis['score']}".strip(),
                          basis=basis, bare_surname=bare_used)
    if won is False:
        return Settlement("LOST", f"settled from {basis['source']} "
                          f"{basis['match_date']} {basis['score']}".strip(),
                          basis=basis, bare_surname=bare_used)
    return Settlement("PENDING", "winner does not resolve selection",
                      basis=basis)
