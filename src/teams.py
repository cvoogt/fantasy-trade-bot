"""Canonical NFL team abbreviations.

Every upstream spells teams differently — MFL's players export uses three-letter
codes (KCC, TBB, SFO), FantasyCalc and Sleeper use the short forms (KC, TB, SF),
and ESPN throws in WSH. Joining two sources on a raw abbreviation silently drops
whichever teams disagree, so normalize both sides through here first.
"""

# Anything not listed passes through unchanged (most codes already agree).
_ALIASES = {
    # MFL's three-letter forms
    "GBP": "GB", "JAC": "JAX", "KCC": "KC", "LVR": "LV", "NEP": "NE",
    "NOS": "NO", "SFO": "SF", "TBB": "TB",
    # Washington's many names
    "WSH": "WAS", "WFT": "WAS",
    # Relocations and other stragglers
    "LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV",
    "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU",
}

# Values that mean "no NFL team", not a team code.
_NON_TEAMS = {"", "FA", "FA*", "NONE", "NFL", "RET"}


def normalize(team: str | None) -> str:
    """Canonical short code ('KCC' -> 'KC'), or '' when there's no team."""
    if not team:
        return ""
    code = str(team).strip().upper()
    if code in _NON_TEAMS:
        return ""
    return _ALIASES.get(code, code)


def same_team(a: str | None, b: str | None) -> bool:
    """True when two abbreviations refer to the same NFL team."""
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb
