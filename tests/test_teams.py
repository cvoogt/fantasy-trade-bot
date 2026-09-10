"""Tests for canonical NFL team abbreviations."""
from src.teams import normalize, same_team


def test_mfl_three_letter_codes_normalize():
    for mfl, canon in (("KCC", "KC"), ("TBB", "TB"), ("SFO", "SF"),
                       ("GBP", "GB"), ("NEP", "NE"), ("NOS", "NO"),
                       ("LVR", "LV"), ("JAC", "JAX")):
        assert normalize(mfl) == canon


def test_already_canonical_passes_through():
    for code in ("KC", "DAL", "PHI", "CIN", "BUF"):
        assert normalize(code) == code


def test_washington_variants():
    assert normalize("WSH") == normalize("WFT") == normalize("WAS") == "WAS"


def test_relocations():
    assert normalize("OAK") == "LV"
    assert normalize("SD") == "LAC"
    assert normalize("STL") == normalize("LA") == "LAR"


def test_non_teams_become_empty():
    for junk in ("", None, "FA", "fa", " none ", "RET"):
        assert normalize(junk) == ""


def test_case_and_whitespace_insensitive():
    assert normalize("  kcc ") == "KC"


def test_same_team():
    assert same_team("KCC", "KC")
    assert same_team("wsh", "WAS")
    assert not same_team("KC", "DAL")
    assert not same_team("", "")        # no team is not a match
