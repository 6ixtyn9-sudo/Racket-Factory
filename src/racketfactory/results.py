"""Strict parsing of ForeTennis's completed, home/away set-total field."""
import re


def foretennis_set_totals(value):
    text = str(value).strip()
    # Pandas float coercion of an originally two-digit field (e.g. 21.0).
    if re.fullmatch(r'\d{2}\.0', text):
        text = text[:-2]
    if not re.fullmatch(r'(20|21|02|12|30|31|32|03|13|23)', text):
        return None
    return int(text[0]), int(text[1])


def foretennis_winner_side(value):
    pair = foretennis_set_totals(value)
    return None if pair is None else ('player_a' if pair[0] > pair[1] else 'player_b')
