"""Trade scorer: score a two-sided trade by dynasty value, salary, and positional fit.

Convention: side1 and side2 are the two packages being exchanged.
`side1_owner` gives away side1 and receives side2 (and vice versa).
Verdict is reported from the perspective of which side comes out ahead on value.
"""
from dataclasses import dataclass, field
from src.config import LOPSIDED_THRESHOLD

LEAN_THRESHOLD = 0.05


@dataclass
class SideResult:
    player_ids: list[str]
    players: list[dict] = field(default_factory=list)
    total_value: float = 0.0
    total_salary: float = 0.0
    unmatched: list[str] = field(default_factory=list)


@dataclass
class TradeResult:
    side1: SideResult
    side2: SideResult
    value_delta: float          # side1_value - side2_value
    value_delta_pct: float      # relative to the larger package
    salary_delta: float         # side1_salary - side2_salary
    favored: int                # 1, 2, or 0 (fair)
    verdict: str                # FAIR / LEAN / FLEECE-OVERPAY
    positional_flags: list[str] = field(default_factory=list)


def _build_side(player_ids: list[str], value_map: dict, pick_resolver=None) -> SideResult:
    side = SideResult(player_ids=player_ids)
    for pid in player_ids:
        info = value_map.get(pid)
        if info is None and pick_resolver is not None:
            info = pick_resolver(pid)
        if not info:
            side.unmatched.append(pid)
            continue
        side.players.append({"mfl_id": pid, **info})
        side.total_value += info["dynasty_value"]
        side.total_salary += info["salary"]
    return side


def score_trade(
    side1_ids: list[str],
    side2_ids: list[str],
    value_map: dict,
    side1_owner: str | None = None,
    side2_owner: str | None = None,
    thin_lookup=None,
    pick_resolver=None,
) -> TradeResult:
    """Score a trade. `value_map` is {mfl_id: {...}} from value_engine.get_value_map().

    Optional `thin_lookup(franchise_id) -> set[str]` flags a side shipping a
    position they're already thin at. Optional `pick_resolver(token) -> info|None`
    values draft-pick tokens (FP_/DP_) that aren't in value_map.
    """
    s1 = _build_side(side1_ids, value_map, pick_resolver)
    s2 = _build_side(side2_ids, value_map, pick_resolver)

    value_delta = s1.total_value - s2.total_value
    salary_delta = s1.total_salary - s2.total_salary
    larger = max(s1.total_value, s2.total_value, 1.0)
    value_delta_pct = abs(value_delta) / larger

    # Owner of side1 gives up side1 and receives side2 — they come out ahead
    # when they gave up the SMALLER package (value_delta < 0). favored names
    # the winning side (the receiver of more value), not the bigger package.
    if value_delta_pct < LEAN_THRESHOLD:
        favored, verdict = 0, "FAIR"
    elif value_delta_pct < LOPSIDED_THRESHOLD:
        favored = 1 if value_delta < 0 else 2
        verdict = "LEAN"
    else:
        favored = 1 if value_delta < 0 else 2
        verdict = "FLEECE-OVERPAY"

    flags: list[str] = []
    if thin_lookup is not None:
        from src.roster import group_of
        for owner, side in ((side1_owner, s1), (side2_owner, s2)):
            if owner is None:
                continue
            thin = thin_lookup(owner)
            shipped = {group_of(p["position"]) for p in side.players}
            for pos in shipped & thin:
                flags.append(f"Franchise {owner} ships {pos} but is thin there")

    return TradeResult(
        side1=s1,
        side2=s2,
        value_delta=value_delta,
        value_delta_pct=value_delta_pct,
        salary_delta=salary_delta,
        favored=favored,
        verdict=verdict,
        positional_flags=flags,
    )


def format_result(result: TradeResult) -> str:
    lines = []

    def side_block(label: str, s: SideResult) -> str:
        rows = "\n".join(
            f"    {p['name']:<22} {p['position']:<4} "
            f"val={p['dynasty_value']:>7.0f}  ${p['salary']:>9.0f}"
            for p in s.players
        )
        unm = f"\n    [unmatched: {', '.join(s.unmatched)}]" if s.unmatched else ""
        return (
            f"{label} gives up: value={s.total_value:.0f}  salary=${s.total_salary:.0f}\n"
            f"{rows}{unm}"
        )

    s1, s2 = result.side1, result.side2
    lines.append(side_block("SIDE 1", s1))
    lines.append(side_block("SIDE 2", s2))
    lines.append("")
    lines.append(f"Side 1 net value (gets - gives): {s2.total_value - s1.total_value:+.0f}")
    lines.append(f"Side 2 net value (gets - gives): {s1.total_value - s2.total_value:+.0f}")
    lines.append(
        f"Value gap: {result.value_delta_pct*100:.1f}%   "
        f"Salary delta (S1-S2): {result.salary_delta:+.0f}"
    )

    if result.verdict == "FAIR":
        lines.append("Verdict: FAIR")
    elif result.verdict == "LEAN":
        lines.append(f"Verdict: LEAN — favors Side {result.favored}")
    else:
        loser = 2 if result.favored == 1 else 1
        lines.append(
            f"Verdict: FLEECE for Side {result.favored} / OVERPAY for Side {loser}"
        )

    for flag in result.positional_flags:
        lines.append(f"  [!] {flag}")

    return "\n".join(lines)
