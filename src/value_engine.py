import pandas as pd
from src.db import get_conn
from src import mfl_api, fantasycalc_api

STARTERS_BY_POS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "PK": 1, "Def": 1}


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


def get_value_map(df: pd.DataFrame | None = None) -> dict[str, dict]:
    """Return {mfl_id: {name, position, dynasty_value, salary, value_per_dollar, vor}}."""
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
    return out
