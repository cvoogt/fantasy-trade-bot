import re
import pandas as pd
from src.db import get_conn
from src import mfl_api, fantasycalc_api

STARTERS_BY_POS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "PK": 1, "Def": 1}

_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}
# Upcoming rookie draft year used for slotted (DP_) picks. FantasyCalc only
# slots one draft class; future picks (FP_) carry their own year.
PICK_DRAFT_YEAR = 2026


def build_player_values() -> pd.DataFrame:
    """Build master player values table with dynasty value, salary, VPD, and VOR."""
    fc_values = fantasycalc_api.get_cached_values()
    if not fc_values:
        fantasycalc_api.fetch_and_cache()
        fc_values = fantasycalc_api.get_cached_values()

    conn = get_conn()
    crosswalk = pd.read_sql("SELECT * FROM crosswalk", conn)
    conn.close()

    fc_df = pd.DataFrame(fc_values)
    salaries = mfl_api.get_salaries()
    sal_df = pd.DataFrame(salaries).rename(columns={"id": "mfl_id", "salary": "salary"})
    if not sal_df.empty:
        sal_df["salary"] = pd.to_numeric(sal_df["salary"], errors="coerce").fillna(0)

    merged = crosswalk.merge(fc_df, on="fc_name", how="left", suffixes=("", "_fc"))
    if not sal_df.empty:
        merged = merged.merge(sal_df[["mfl_id", "salary"]], on="mfl_id", how="left")
    else:
        merged["salary"] = 0

    merged["salary"] = merged["salary"].fillna(0)
    merged["dynasty_value"] = merged["dynasty_value"].fillna(0)

    merged["value_per_dollar"] = merged.apply(
        lambda r: r["dynasty_value"] / r["salary"] if r["salary"] > 0 else 0, axis=1
    )

    merged["vor"] = 0.0
    for pos, num_starters in STARTERS_BY_POS.items():
        mask = merged["position"] == pos
        pos_players = merged.loc[mask].sort_values("dynasty_value", ascending=False)
        if len(pos_players) == 0:
            continue
        num_teams = 12
        replacement_idx = min(num_starters * num_teams, len(pos_players) - 1)
        replacement_value = pos_players.iloc[replacement_idx]["dynasty_value"]
        merged.loc[mask, "vor"] = merged.loc[mask, "dynasty_value"] - replacement_value

    result = merged[
        ["mfl_id", "mfl_name", "fc_name", "position", "team", "dynasty_value",
         "overall_rank", "salary", "value_per_dollar", "vor"]
    ].sort_values("dynasty_value", ascending=False)

    return result


def dump_csv(path: str = "player_values.csv"):
    df = build_player_values()
    df.to_csv(path, index=False)
    print(f"Wrote {len(df)} players to {path}")
    return df


def get_pick_value_map() -> dict[str, float]:
    """{FantasyCalc pick name: dynasty_value}, e.g. '2026 3rd' -> 1108."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT fc_name, dynasty_value FROM fantasycalc_cache WHERE position = 'PICK'"
    ).fetchall()
    conn.close()
    return {r["fc_name"]: float(r["dynasty_value"]) for r in rows}


def _pick_info(name: str, val: float) -> dict:
    return {"name": name, "position": "PICK", "dynasty_value": float(val),
            "salary": 0.0, "value_per_dollar": 0.0, "vor": 0.0}


def make_pick_resolver(pick_map: dict[str, float] | None = None):
    """Return resolve(token) -> player-info dict for MFL pick tokens, else None.

    Handles future picks (FP_<franchise>_<year>_<round> -> generic round value)
    and slotted picks (DP_<round0>_<pick0>, 0-indexed -> '{year} Pick R.PP').
    """
    if pick_map is None:
        pick_map = get_pick_value_map()

    def resolve(token: str) -> dict | None:
        m = re.match(r"FP_\d+_(\d{4})_(\d+)$", token)
        if m:
            year, rnd = int(m.group(1)), int(m.group(2))
            name = f"{year} {_ORD.get(rnd, f'{rnd}th')}"
            val = pick_map.get(name)
            return _pick_info(name, val) if val is not None else None

        m = re.match(r"DP_(\d+)_(\d+)$", token)
        if m:
            rnd, pick = int(m.group(1)) + 1, int(m.group(2)) + 1
            name = f"{PICK_DRAFT_YEAR} Pick {rnd}.{pick:02d}"
            val = pick_map.get(name)
            if val is None:  # fall back to generic round value
                name = f"{PICK_DRAFT_YEAR} {_ORD.get(rnd, f'{rnd}th')}"
                val = pick_map.get(name)
            return _pick_info(name, val) if val is not None else None

        # Typed pick names: '2026 1st', '2026 pick 1.01', '2027 2nd', ...
        lowered = {k.lower(): k for k in pick_map}
        key = lowered.get(token.strip().lower())
        if key:
            return _pick_info(key, pick_map[key])

        return None

    return resolve


def get_value_map(df: pd.DataFrame | None = None) -> dict[str, dict]:
    """Return {mfl_id: {name, position, dynasty_value, salary, value_per_dollar, vor}}.

    Offense comes from FantasyCalc; IDP players (which FantasyCalc doesn't
    cover) are synthesized from league-scored production — see idp_values.py."""
    if df is None:
        df = build_player_values()
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        out[str(r["mfl_id"])] = {
            "name": r["mfl_name"],
            "position": r["position"],
            "dynasty_value": float(r["dynasty_value"]),
            "salary": float(r["salary"]),
            "value_per_dollar": float(r["value_per_dollar"]),
            "vor": float(r["vor"]),
        }
    _augment_idp(out)
    return out


# IDP starters per position group in this league (group minimums x 10 teams
# sets the replacement level for VOR).
_IDP_GROUPS = {"DT": "DL", "DE": "DL", "LB": "LB", "CB": "DB", "S": "DB"}
_IDP_GROUP_STARTERS = {"DL": 3, "LB": 3, "DB": 3}
_NUM_TEAMS = 10


def _augment_idp(out: dict[str, dict]):
    """Add synthesized entries for IDP players missing from the value map."""
    try:
        from src.idp_values import compute_idp_values
        idp = compute_idp_values()
    except Exception:
        return  # offense-only map is still usable

    salaries = {}
    try:
        for p in mfl_api.get_salaries():
            salaries[p.get("id", "")] = float(p.get("salary") or 0)
    except Exception:
        pass

    by_group: dict[str, list[float]] = {}
    for v in idp.values():
        by_group.setdefault(_IDP_GROUPS[v["position"]], []).append(v["dynasty_value"])
    replacement = {}
    for group, vals in by_group.items():
        vals.sort(reverse=True)
        idx = min(_IDP_GROUP_STARTERS[group] * _NUM_TEAMS, len(vals) - 1)
        replacement[group] = vals[idx]

    for mfl_id, v in idp.items():
        if mfl_id in out:
            continue
        sal = salaries.get(mfl_id, 0.0)
        val = float(v["dynasty_value"])
        out[mfl_id] = {
            "name": v["name"],
            "position": v["position"],
            "dynasty_value": val,
            "salary": sal,
            "value_per_dollar": val / sal if sal > 0 else 0.0,
            "vor": val - replacement[_IDP_GROUPS[v["position"]]],
        }
