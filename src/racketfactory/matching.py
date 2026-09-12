"""Player identity matching shared by pricing and settlement.

Supports source initials at either end, compound surnames and doubles teams.
Never discard a conflicting initial (A. Zverev is not M. Zverev).
Callers must also match the opponent and event date, not just one player.
"""
import re
import unicodedata


def _tokens(value):
    text = unicodedata.normalize('NFKD', str(value))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    tokens = re.findall(r'[a-z]+', text.lower())
    # TennisExplorer/TennisData: surname followed by one or more initials.
    if tokens and len(tokens[0]) > 1:
        trailing = []
        while tokens and len(tokens[-1]) == 1:
            trailing.insert(0, tokens.pop())
        tokens = trailing + tokens
    # Reviewed alias: https://www.tennisexplorer.com/player/burillo-escorihuela/
    # BetClan shortens Irene Burillo Escorihuela to Irene Burillo.
    if tokens in (["irene", "burillo"], ["i", "burillo"]):
        tokens = tokens + ["escorihuela"]
    return tokens


def _person(a, b):
    a, b = _tokens(a), _tokens(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # Remove the identical surname suffix, but retain given names/initials.
    n = 0
    while n < min(len(a), len(b)) and a[-n-1] == b[-n-1] and len(a[-n-1]) > 1:
        n += 1
    if not n:
        return False
    pa, pb = a[:-n], b[:-n]
    if not pa or not pb:
        # Surname-only representations are common for doubles teams.
        return True
    # Compare given names in order; extra middle names are allowed, conflicting
    # forenames are not. Initials must agree with the corresponding full name.
    for x, y in zip(pa, pb):
        if x != y and not ((len(x) == 1 or len(y) == 1) and x[0] == y[0]):
            return False
    return True


def names_match(a, b):
    if str(a).strip().lower() in {'', 'none', 'nan', '<na>'} or str(b).strip().lower() in {'', 'none', 'nan', '<na>'}:
        return False
    aa = re.split(r'\s*(?:/|&|\band\b)\s*', str(a), flags=re.I)
    bb = re.split(r'\s*(?:/|&|\band\b)\s*', str(b), flags=re.I)
    if len(aa) != len(bb):
        return False
    if len(aa) == 1:
        return _person(a, b)
    if len(aa) != 2:
        return False
    return (all(_person(x, y) for x, y in zip(aa, bb)) or
            all(_person(x, y) for x, y in zip(aa, reversed(bb))))
