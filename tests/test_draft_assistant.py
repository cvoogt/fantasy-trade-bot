from src.draft_assistant import DraftPick, draft_state


def _pick(rnd, pick, franchise, player=None):
    return DraftPick(round=rnd, pick=pick, franchise=franchise,
                     player=player, timestamp=1 if player else None)


def test_no_draft_when_all_pending():
    # Pre-draft: board exists but nothing picked yet -> not active
    picks = [_pick(1, 1, "0004"), _pick(1, 2, "0009")]
    st = draft_state(picks)
    assert not st["active"]


def test_on_clock_is_first_pending():
    picks = [
        _pick(1, 1, "0004", "17042"),
        _pick(1, 2, "0002"),
        _pick(1, 3, "0006"),
    ]
    st = draft_state(picks)
    assert st["active"]
    assert st["on_clock"].label == "1.02"
    assert st["my_turn"]  # MFL_FRANCHISE_ID defaults to 0002
    assert st["drafted_ids"] == {"17042"}


def test_completed_draft_inactive():
    picks = [_pick(1, 1, "0004", "17042"), _pick(1, 2, "0002", "17044")]
    st = draft_state(picks)
    assert not st["active"]
    assert st["on_clock"] is None


def test_my_remaining_picks():
    picks = [
        _pick(1, 1, "0004", "17042"),
        _pick(1, 2, "0006"),
        _pick(2, 5, "0002"),
        _pick(3, 5, "0002"),
    ]
    st = draft_state(picks)
    assert not st["my_turn"]
    assert [p.label for p in st["my_remaining"]] == ["2.05", "3.05"]
